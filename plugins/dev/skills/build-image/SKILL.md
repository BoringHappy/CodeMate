---
name: build-image
description: Build and push OCI/Docker images rootlessly with buildah and skopeo, no Docker daemon required. Use when the user wants to build a container image inside CodeMate, push to a registry, or copy images between registries without needing /var/run/docker.sock or --privileged.
context: fork
---

# Build Container Image (Rootless)

Build and push OCI/Docker images using `buildah` and `skopeo`. Both tools are daemonless, work rootlessly, and require no host Docker socket or `--privileged` flag.

## Tooling check

!```bash
for bin in buildah skopeo fuse-overlayfs newuidmap; do
  if command -v "$bin" >/dev/null 2>&1; then
    echo "✓ $bin"
  else
    echo "✗ $bin (missing)"
  fi
done
```

## Build from a Dockerfile

```bash
buildah build -t myimage:tag -f Dockerfile .
```

- `-t` sets the image tag.
- `-f` is the Dockerfile (defaults to `./Dockerfile`).
- The last argument is the build context.

## Multi-arch build

```bash
buildah build \
  --platform linux/amd64,linux/arm64 \
  --manifest myimage:tag \
  .
```

`--manifest` produces a manifest list referencing per-arch images.

## Push to a registry

Authenticate first (writes to `~/.config/containers/auth.json`). Prefer
`--password-stdin` so the token never lands in shell history:

```bash
echo "$GH_TOKEN" | buildah login -u USERNAME --password-stdin ghcr.io
```

Push a single image:

```bash
buildah push myimage:tag docker://ghcr.io/org/myimage:tag
```

Push a multi-arch manifest:

```bash
buildah manifest push --all myimage:tag docker://ghcr.io/org/myimage:tag
```

## Copy or inspect images with skopeo

Copy between registries without pulling locally:

```bash
skopeo copy docker://src.example.com/img:tag docker://dst.example.com/img:tag
```

Inspect remotely:

```bash
skopeo inspect docker://ghcr.io/org/myimage:tag
```

## List local images

```bash
buildah images
```

## Notes

- No Docker daemon and no `--privileged` flag is required.
- Rootless user namespaces are backed by `/etc/subuid` and `/etc/subgid` entries for the `agent` user (configured in the base image).
- Storage uses `fuse-overlayfs` for performant rootless overlays.
- `buildah build` accepts standard Dockerfile syntax.
