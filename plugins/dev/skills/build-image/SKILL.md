---
name: build-image
description: Build and push Docker/OCI images via the Docker CLI talking to the host daemon over a mounted /var/run/docker.sock. Use when the user wants to build a container image inside CodeMate or push it to a registry.
context: fork
---

# Build Container Image (Docker CLI)

Build and push images using the official Docker CLI (`docker` + `docker buildx`). The CLI is installed in the base image; the daemon is the host's, reached through a mounted `/var/run/docker.sock`.

## Runtime requirements

- The CodeMate container must be launched with `-v /var/run/docker.sock:/var/run/docker.sock`.
- The `agent` user needs permission to read/write that socket. The launcher should pass `--group-add $(stat -c %g /var/run/docker.sock)` so `agent`'s supplementary groups include the host docker group; otherwise `sudo docker ...` works as a fallback.

## Tooling check

!```bash
for bin in docker; do
  if command -v "$bin" >/dev/null 2>&1; then
    echo "✓ $bin ($("$bin" --version 2>/dev/null | head -1))"
  else
    echo "✗ $bin (missing)"
  fi
done
if docker buildx version >/dev/null 2>&1; then
  echo "✓ docker buildx ($(docker buildx version | head -1))"
else
  echo "✗ docker buildx (missing)"
fi
if docker info >/dev/null 2>&1; then
  echo "✓ daemon reachable"
else
  echo "✗ daemon not reachable (is /var/run/docker.sock mounted and readable?)"
fi
```

## Build from a Dockerfile

```bash
docker build -t myimage:tag -f Dockerfile .
```

- `-t` sets the image tag.
- `-f` is the Dockerfile (defaults to `./Dockerfile`).
- The last argument is the build context.

## Multi-arch build with buildx

```bash
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t registry.example.com/org/myimage:tag \
  --push \
  .
```

`--push` is required for multi-arch builds because the local image store can only hold a single platform. The host daemon must have QEMU set up (`docker run --privileged --rm tonistiigi/binfmt --install all`, once per host) for cross-platform builds.

## Push to a registry

Authenticate first (writes to `~/.docker/config.json`). Prefer `--password-stdin` so the token never lands in shell history:

```bash
echo "$GH_TOKEN" | docker login -u USERNAME --password-stdin ghcr.io
```

Push a single image:

```bash
docker push ghcr.io/org/myimage:tag
```

## Tag for a different registry

```bash
docker tag myimage:tag registry.datalake.vip/org/myimage:tag
docker push registry.datalake.vip/org/myimage:tag
```

## Inspect images

Local:

```bash
docker image inspect myimage:tag
```

Remote (without pulling the full image):

```bash
docker buildx imagetools inspect registry.example.com/org/myimage:tag
```

## List local images

```bash
docker images
```

## Notes

- The container does not run its own dockerd. All builds, pushes, and image storage live on the host daemon reached through the mounted socket.
- Build context is streamed from the CLI to the daemon, so paths in the build command are resolved against the container's filesystem.
- For private registries with self-signed certs, configure the host daemon's `/etc/docker/daemon.json` with `insecure-registries` — that is a host-side change, not something to do from inside CodeMate.
