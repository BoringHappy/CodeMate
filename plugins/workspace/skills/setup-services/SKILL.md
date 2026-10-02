---
name: setup-services
description: Create or update a repository's .codemate/config.yaml so CodeMate installs dependencies and starts project services automatically when its container starts. Use when configuring project setup commands, development servers, or service readiness checks for CodeMate.
---

# Setup Project Services

Create a working `.codemate/config.yaml` in the user's repository using its
actual development commands. Use `workspace:setup-services` in Codex or
`/workspace:setup-services` in Claude Code.

## Inspect the repository

Find the Git worktree root, including when invoked from a subdirectory. Read
existing `.codemate/config.yaml`, repository instructions, development docs,
package manifests, lockfiles, and relevant launch scripts. Preserve existing
configuration and comments when updating it. `.codemate` is a directory; if
that path is already a regular file, resolve the conflict without overwriting it.

Infer the services and dependency installation from that evidence. Respect the
repository's package manager and monorepo layout: for example, use `npm ci`
only with an npm lockfile, and install shared workspace dependencies at the
workspace root rather than once per package. Reuse documented scripts and
entrypoints. Ask only when a missing choice prevents a usable configuration,
such as which of several independent applications the user wants to run.

## Write the configuration

The supported schema is:

| Location | Supported fields and defaults |
|---|---|
| Root | Optional `version: 1`, `setup` list, and `services` mapping |
| `setup[]` | Required `run`; optional `cwd: .` and `timeout: 300` |
| `services.<name>` | Required `command`; optional `cwd: .` and `ready` |
| `services.<name>.ready` | Required `http` URL; optional `timeout: 30` |

Timeouts are positive numbers of seconds. Commands are non-empty strings.
Service names match `[A-Za-z][A-Za-z0-9_-]*`; `all` is reserved. All `cwd`
values are relative to the repository root and must stay inside it. Duplicate
keys and unknown fields are rejected. There are no `env`, `depends_on`,
`ports`, or `preview.routes` fields.

- Setup commands run sequentially in separate Bash processes on each container
  start. Keep them repeatable and noninteractive. Use setup for dependency
  installation and required preparation, not long-running servers. Do not add
  destructive database resets or production operations as routine setup.
- Services start together under Supervisor after setup succeeds; their order
  in YAML does not establish dependencies. Keep each command in the foreground,
  without `&`, `nohup`, or daemon mode. Reuse the application's existing retry
  behavior for dependencies; report any missing dependency that prevents startup.
- Commands inherit the container environment. An `export` in one setup step
  does not persist into another step or a service. Reference required environment
  variables without committing secret values; there is no automatic loading of
  arbitrary repository `.env` files by the service manager.
- Choose stable, nonconflicting service ports and supported bind flags. For a
  container web server, use `0.0.0.0` when appropriate and probe it through
  `127.0.0.1`. Disable automatic port fallback when the framework supports it
  (for example, Vite's `--strictPort`). This configuration does not publish
  Docker ports or provide a preview proxy.
- `ready.http` must be an actual HTTP(S) endpoint returning 2xx. Check the
  repository for its health route; do not invent `/health`. An existing public
  page can be used instead. Omit `ready` for a worker without an HTTP endpoint;
  without a probe, startup only checks that the process stays running for one
  second. Increase the readiness timeout when the project's startup needs it.

Example for a repository that actually has a uv-managed FastAPI backend with
`/health` and an npm-managed Vite frontend. Adapt paths, scripts, entrypoints,
and probes to the inspected repository; do not add both services by default:

```yaml
version: 1

setup:
  - cwd: backend
    run: uv sync
  - cwd: frontend
    run: npm ci

services:
  api:
    cwd: backend
    command: uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
    ready:
      http: http://127.0.0.1:8000/health
      timeout: 30
  web:
    cwd: frontend
    command: npm run dev -- --host 0.0.0.0 --port 3000 --strictPort
    ready:
      http: http://127.0.0.1:3000/
      timeout: 60
```

## Validate and explain activation

Check the YAML against the schema above, confirm each working directory exists
or is created by an earlier setup step, and verify script names, entrypoints,
CLI flags, ports, and readiness routes against the repository. A YAML syntax
check alone does not establish that commands will start successfully.

When the user requests a startup test, use the available development environment
and inspect the resulting processes and HTTP probes. If CodeMate or required
dependencies are unavailable, still produce the configuration and state which
checks remain unverified. Do not require GitHub authentication or an open PR to
create this file.

Configuration is loaded on the next standard CodeMate container start;
`--shell` and `--chat` also run setup, while `--pure` does not. Reattaching or
resuming an agent does not reload configuration. `codemate-services restart`
reuses the already loaded definitions and does not rerun setup. Do not stop the
current agent's container merely to apply a newly written configuration.

Once the container has loaded the configuration, these commands work from
anywhere in that repository (replace `web` with a configured service name):

```bash
codemate-services status --json
codemate-services logs web
codemate-services restart web
```

`status --json` reports the currently loaded processes and readiness checks;
it does not validate or apply edits to the YAML. It also supplies log locations:
`setup.log` contains setup output and `startup.log` contains configuration or
startup errors. A setup failure skips remaining setup and service startup but
still opens the agent; a readiness timeout leaves the service process running.
Workspace SessionStart hooks report live service status to Codex and Claude,
without launching services themselves.

Finish with the config path, configured services and ports, required environment
variable names, validation performed, and how to activate the configuration.
Commit or push only when requested or required by the repository workflow.
