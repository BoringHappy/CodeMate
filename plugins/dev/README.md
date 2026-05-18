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

Build and push Docker/OCI images using the official Docker CLI talking to the host's daemon via a mounted `/var/run/docker.sock`.

**Usage:**
- Build images from a `Dockerfile` with `docker build`
- Multi-arch builds (`linux/amd64`, `linux/arm64`) with `docker buildx build --platform ... --push`
- Push to registries with `docker push`
- Inspect remote images with `docker buildx imagetools inspect`

**Runtime requirements:**
- Launch CodeMate with the Docker socket mounted and the host's docker group passed through:
  ```bash
  codemate --branch YOUR_BRANCH \
    --mount /var/run/docker.sock:/var/run/docker.sock \
    --docker-param "--group-add $(stat -c %g /var/run/docker.sock)"
  ```
  Or set `CODEMATE_MOUNTS` + `DOCKER_PARAMS` in `.env` for the same effect on every run. Fall back to `sudo docker ...` if you skip `--group-add`.
- **Security:** Mounting `/var/run/docker.sock` grants root-equivalent control of the host daemon to anything running inside CodeMate. Only use this on trusted hosts.

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
