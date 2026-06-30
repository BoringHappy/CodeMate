---
name: manage-k8s
description: Interact with Kubernetes clusters using kubectl and helm. Use when the user wants to inspect cluster state, deploy or modify resources, install or upgrade Helm charts, view logs, exec into pods, or troubleshoot workloads.
context: fork
---

# Manage Kubernetes (kubectl + helm)

Inspect, deploy, and manage Kubernetes resources with `kubectl` and `helm`. Both are pre-installed in the base image (latest stable, multi-arch).

## Prepare

CodeMate mounts `~/.codemate` by default and links `~/.codemate/.kube` to `~/.kube` inside the container. Put kubeconfig files under `~/.codemate/.kube` on the host before launching CodeMate.

**Default kubeconfig setup:**

```bash
mkdir -p ~/.codemate/.kube
cp ~/.kube/config ~/.codemate/.kube/config
codemate --branch YOUR_BRANCH
```

You can still override the default with an explicit mount when needed:

```bash
codemate --branch YOUR_BRANCH \
  --mount ~/.kube:/home/agent/.kube
```

If the kubeconfig references TLS client certs or token files outside `~/.kube`, mount those paths too (or use `--mount` per file).

**Alternative: a single file with `KUBECONFIG`.** Mount one config file and point the env var at it:

```bash
codemate --branch YOUR_BRANCH \
  --mount /path/to/kubeconfig:/home/agent/.kube/config
```

**Verify cluster access before doing anything destructive:**

```bash
kubectl config current-context
kubectl cluster-info
```

If `kubectl` reports `Unable to connect to the server` or `error loading config file`, stop and tell the user what's missing — do not try to work around it.

## Tooling check

!```bash
if command -v kubectl >/dev/null 2>&1; then
  kubectl version --client --output=yaml 2>/dev/null | head -5
else
  echo "✗ kubectl missing"
fi
if command -v helm >/dev/null 2>&1; then
  helm version --short
else
  echo "✗ helm missing"
fi
```

## kubectl

### Cluster + context

```bash
kubectl config current-context
kubectl config get-contexts
kubectl cluster-info
```

### Get resources

```bash
kubectl get pods -n NAMESPACE
kubectl get all -n NAMESPACE
kubectl get pods -A                  # all namespaces
kubectl get nodes -o wide
```

### Describe / explain

```bash
kubectl describe pod POD -n NAMESPACE
kubectl explain deployment.spec.template.spec.containers
```

### Logs

```bash
kubectl logs POD -n NAMESPACE
kubectl logs -f POD -n NAMESPACE              # follow
kubectl logs POD -c CONTAINER -n NAMESPACE    # specific container
kubectl logs --previous POD -n NAMESPACE      # crashed container
```

### Exec into a pod

```bash
kubectl exec -it POD -n NAMESPACE -- /bin/sh
```

Distroless or minimal images often lack `sh`/`bash`. Fallbacks:

```bash
kubectl exec -it POD -n NAMESPACE -- /bin/bash
kubectl debug -it POD -n NAMESPACE --image=busybox --target=CONTAINER
```

### Apply / diff / delete

```bash
kubectl diff -f manifest.yaml     # preview before apply
kubectl apply -f manifest.yaml
kubectl delete -f manifest.yaml
```

### Port-forward

```bash
kubectl port-forward svc/SERVICE 8080:80 -n NAMESPACE
```

## helm

### Repos

```bash
helm repo add NAME URL
helm repo update
helm search repo NAME
```

### Install / upgrade

```bash
helm install RELEASE chart/path -n NAMESPACE --create-namespace
helm upgrade --install RELEASE chart/path -n NAMESPACE -f values.yaml
```

`--install` makes `upgrade` idempotent (installs if the release is missing).

### Render before applying

```bash
helm template RELEASE chart/path -f values.yaml > rendered.yaml
```

For richer diffs, install the `helm-diff` plugin first (not bundled in the
base image):

```bash
helm plugin install https://github.com/databus23/helm-diff
helm diff upgrade RELEASE chart/path -f values.yaml
```

### List / inspect / rollback

```bash
helm list -n NAMESPACE
helm get values RELEASE -n NAMESPACE
helm history RELEASE -n NAMESPACE
helm rollback RELEASE REVISION -n NAMESPACE
```

### Uninstall

```bash
helm uninstall RELEASE -n NAMESPACE
```

## Notes

- Prefer `kubectl diff`, `helm template`, or `helm diff` before applying changes to production clusters.
- Both binaries are multi-arch (amd64/arm64).
