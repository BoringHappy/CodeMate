---
name: run-image
description: Run an existing container image as a Kubernetes pod with local files injected from the CodeMate workspace, then exec commands against it. Use when the user wants to launch a workload from a registry image inside a live cluster and feed it scripts, configs, or data from disk — no local Docker daemon required.
context: fork
---

# Run a Container Image in Kubernetes with Local Files

Launch an existing image as a pod and feed it local files from the CodeMate workspace. The cluster does the running; `kubectl` is the only client needed.

## Prepare

This skill needs a working kubeconfig inside the container — the same setup as `/dev:manage-k8s`. Quick sanity check:

```bash
kubectl config current-context && kubectl cluster-info
```

If those fail, mount your kubeconfig (see the `/dev:manage-k8s` Prepare section) and rerun.

**Image requirement:** the target image must already exist in a registry the cluster can pull from. For private registries, the namespace needs a matching `imagePullSecrets`.

**Always verify context before launching workloads:**

```bash
kubectl config current-context
kubectl config view --minify -o jsonpath='{..namespace}{"\n"}'
```

If the context or namespace doesn't match the user's expectation, stop and confirm.

## Path A — kubectl run + cp + exec (multiple files, image must have `tar`)

Use this when the image has `tar` on `$PATH` (most distro-based images do). `kubectl cp` pipes a tarball through `kubectl exec`, so without `tar` the copy step fails.

```bash
IMG=registry.example.com/org/myimage:tag    # replace with your image
POD=run-$(date +%s)
NS=default

# Optional but recommended for scripted use: clean up the pod even if a step fails.
trap 'kubectl delete pod "$POD" -n "$NS" --ignore-not-found' EXIT

# 1. Launch the pod with an idle command so it stays alive long enough to load files
kubectl run "$POD" \
  --image="$IMG" \
  --restart=Never \
  -n "$NS" \
  --command -- sleep infinity

# 2. Wait until it's ready
kubectl wait --for=condition=Ready "pod/$POD" -n "$NS" --timeout=120s

# 3. Make sure the target directory exists, then inject local files.
#    `/tmp` is always writable; use a different path only if your image
#    explicitly provides a writable directory (e.g. /work, /data).
kubectl exec -n "$NS" "$POD" -- mkdir -p /tmp/inputs
kubectl cp ./script.sh    "$NS/$POD:/tmp/script.sh"
kubectl cp ./inputs/      "$NS/$POD:/tmp/inputs/"
kubectl cp ./config.yaml  "$NS/$POD:/tmp/config.yaml"

# 4. Run the real workload
kubectl exec -n "$NS" "$POD" -- sh -c 'chmod +x /tmp/script.sh && /tmp/script.sh /tmp/inputs'

# 5. Tail logs / inspect outputs
kubectl exec -n "$NS" "$POD" -- ls -la /tmp
kubectl logs -n "$NS" "$POD"

# 6. Clean up explicitly (the EXIT trap above also handles this)
kubectl delete pod "$POD" -n "$NS"
```

**Notes:**
- The unique pod name (timestamp suffix) prevents collisions on re-runs.
- `--restart=Never` makes a single-shot pod, not a Deployment.
- `--command -- sleep infinity` overrides the image's default `CMD` so the pod stays alive long enough to receive files and run commands. If you instead want the image's normal entrypoint to run after files are injected, see Path B's approach.
- `/tmp` is used as the staging directory because it's always writable. If your image declares a different working directory (e.g. `/work`, `/data`, `/app`), substitute it — and `mkdir -p` it first if it doesn't already exist.
- `kubectl cp` accepts the `NS/POD:PATH` form, keeping commands compact.
- The `trap … EXIT` line ensures the pod is deleted even if `kubectl wait` times out or any step fails. If you're running these commands interactively in a shell session, the trap is optional.

## Path B — kubectl run --stdin (single file, no `tar` needed)

Use this for distroless / minimal images where `kubectl cp` fails. The local file is piped to the container's stdin and saved by a small shell wrapper, which then runs the image's real workload.

Replace `<YOUR-ENTRYPOINT>` with the actual command or binary inside the image (e.g. `/usr/local/bin/myapp`, `python /app/main.py`). There is no universal `/entrypoint.sh` path — every image is different, so check the image's `CMD` / `ENTRYPOINT` first (`docker inspect` on a host that has the image, or the image's docs).

```bash
IMG=registry.example.com/org/myimage:tag    # replace with your image
POD=run-$(date +%s)
NS=default

kubectl run "$POD" \
  --image="$IMG" \
  --restart=Never \
  -n "$NS" \
  --stdin=true \
  --rm=true \
  --command -- /bin/sh -c 'cat > /tmp/input.json && <YOUR-ENTRYPOINT> /tmp/input.json' \
  < ./input.json
```

What's happening:
- `--stdin=true` pipes your local file into the container's stdin.
- `cat > /tmp/input.json` saves it to a writable path (`/tmp` is always writable).
- `&& <YOUR-ENTRYPOINT> /tmp/input.json` then runs the image's real command with the saved file.
- `--rm=true` asks kubectl to delete the pod when the command exits.

**Limits:**
- One file per run (stdin is a single stream).
- Image must have at least `sh` and `cat` (i.e., busybox or distroless `:debug` variants). For true `FROM scratch`, pre-bake a wrapper image.
- `--rm=true` requires kubectl to stay attached. When stdin comes from a file (as above) some kubectl versions detach as soon as EOF is hit and skip the cleanup. If you find the pod lingering, follow up with `kubectl delete pod "$POD" -n "$NS" --ignore-not-found`.

## Choosing a path

| Situation | Path |
|---|---|
| Image has `tar`, you want multiple files / directories | A |
| Image is distroless / minimal, single file is enough | B |
| File is large (>1 MiB) and the image is minimal | B (or pre-bake a wrapper image) |

## Common failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| Pod stuck in `ContainerCreating` | image pull failure (private registry, wrong name) | `kubectl describe pod $POD` → check Events; configure `imagePullSecrets`. |
| `tar: not found` during `kubectl cp` | image is distroless / minimal | Switch to Path B (stdin). |
| Pod exits immediately, status `Completed` | `--command -- sleep infinity` was omitted and the image's default CMD ran and finished | Re-add the explicit `sleep infinity` override for Path A. |
| `kubectl exec` fails with `OCI runtime exec failed` | shell binary missing in image | Use `/bin/bash` if `/bin/sh` is absent, or attach a debug container with `kubectl debug -it $POD --image=busybox --target=$CONTAINER`. |
| Read-only filesystem error during `cp` | image hardened with `readOnlyRootFilesystem`, or target path is in the read-only layer | Copy into `/tmp` (usually an `emptyDir`) or override the pod spec to add a writable `emptyDir` volume. |

## Notes

- This is a runtime workflow, not a build workflow. To produce a new image, push from a system with a Docker/buildah toolchain — CodeMate intentionally does not run image builds inside the container.
- Prefer `--restart=Never` for ad-hoc runs so you don't end up with a Deployment + ReplicaSet to clean up.
- For repeat runs against the same image, consider a longer-lived Deployment with a writable `emptyDir` and just `kubectl cp` / `kubectl exec` into it directly.
