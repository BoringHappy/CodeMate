---
name: build-image
description: Build and push Docker/OCI images via the Docker CLI talking to the host daemon over a mounted /var/run/docker.sock. Use when the user wants to build a container image inside CodeMate or push it to a registry.
context: fork
---

# Build Container Image (Docker CLI)

Build and push images using the official Docker CLI (`docker` + `docker buildx`). The CLI is installed in the base image; the daemon is the host's, reached through a mounted `/var/run/docker.sock`.

## Prepare

CodeMate does **not** mount the Docker socket by default. Before this skill can do anything, the user must launch the container with the host's `/var/run/docker.sock` mounted in.

> ⚠️ **Security:** Mounting `/var/run/docker.sock` gives container processes root-equivalent control over the host's Docker daemon — a process inside can `docker run --privileged -v /:/host …` to escape. Only mount the socket on trusted hosts you already control.

**Launch CodeMate with the socket mounted and the docker group passed through:**

```bash
codemate --branch YOUR_BRANCH \
  --mount /var/run/docker.sock:/var/run/docker.sock \
  --docker-param "--group-add $(stat -c %g /var/run/docker.sock)"
```

Or set both once in `.env` so every run picks them up:

```bash
CODEMATE_MOUNTS="/var/run/docker.sock:/var/run/docker.sock"
DOCKER_PARAMS="--group-add 999"   # replace 999 with `stat -c %g /var/run/docker.sock` from the host
```

**Why `--group-add`?** The host socket is typically owned by `root:docker` with mode `0660`. The `agent` user (uid 1000) inside the container needs a supplementary group whose GID matches the host's docker group. If you skip this, fall back to `sudo docker …` — `agent` has passwordless sudo.

**Verify the socket is reachable before building:**

```bash
docker info >/dev/null 2>&1 && echo "✓ daemon reachable" \
  || echo "✗ daemon not reachable — check socket mount and permissions"
```

If verification fails, stop and tell the user what's missing — do not try to work around it.

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
