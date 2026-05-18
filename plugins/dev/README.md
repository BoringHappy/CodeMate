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

### `/dev:run-image`

Run an existing container image inside a live Kubernetes cluster: launch a pod from the image, inject local files from the CodeMate workspace, and exec commands — all without a local Docker daemon or build toolchain.

**Usage:**
- Launch an arbitrary image as a pod with `kubectl run --restart=Never --command -- sleep infinity`
- Inject local files via `kubectl cp` (when the image has `tar`) or `kubectl run --stdin` (when it doesn't)
- Exec the real workload inside the pod with `kubectl exec`

**Runtime requirements:**
- Kubeconfig must be available inside the container (see `/dev:manage-k8s` for the mount setup — the same kubeconfig is reused).
- The target image must already exist in a registry the cluster can pull from.

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

# Run an existing image as a k8s pod with local files
/dev:run-image

# Manage Kubernetes resources
/dev:manage-k8s
```
