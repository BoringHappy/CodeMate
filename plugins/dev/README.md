# Dev Plugin

A CodeMate plugin providing development utilities.

## Skills

### `/dev:read-env-key`

List environment variable keys without exposing their values.

**Usage:**
- List all environment variable keys
- Filter keys by pattern (case-insensitive)
- Check if specific environment variables exist

**Security:**
This skill only reads environment variable names (keys), never their values. This prevents accidental exposure of sensitive information.

### `/dev:build-image`

Build and push OCI/Docker images rootlessly using `buildah` and `skopeo` — no Docker daemon, no `/var/run/docker.sock` mount, no `--privileged` flag.

**Usage:**
- Build images from a `Dockerfile`
- Multi-arch builds (`linux/amd64`, `linux/arm64`) via `buildah --manifest`
- Push to registries with `buildah push`
- Copy or inspect images between registries with `skopeo`

### `/dev:manage-k8s`

Interact with Kubernetes clusters using `kubectl` and `helm`.

**Usage:**
- Inspect cluster state, pods, nodes, and other resources
- View logs, exec into pods, port-forward services
- Apply / diff / delete manifests
- Install, upgrade, rollback, and inspect Helm releases

## Installation

This plugin is designed to be installed via the CodeMate plugin marketplace.

## Examples

```bash
# List environment variable keys
/dev:read-env-key
/dev:read-env-key GIT

# Build and push a container image
/dev:build-image

# Manage Kubernetes resources
/dev:manage-k8s
```
