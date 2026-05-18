---
name: manage-k8s
description: Interact with Kubernetes clusters using kubectl and helm. Use when the user wants to inspect cluster state, deploy or modify resources, install or upgrade Helm charts, view logs, exec into pods, or troubleshoot workloads.
context: fork
---

# Manage Kubernetes (kubectl + helm)

Inspect, deploy, and manage Kubernetes resources with `kubectl` and `helm`. Both are pre-installed in the base image (latest stable, multi-arch).

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

- Cluster access requires a kubeconfig — mount `~/.kube/config` into the container or use a service-account token.
- Prefer `kubectl diff`, `helm template`, or `helm diff` before applying changes to production clusters.
- Both binaries are multi-arch (amd64/arm64).
