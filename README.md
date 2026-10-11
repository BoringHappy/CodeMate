# CodeMate

English | [简体中文](README_CN.md)

Claude Code and Codex workflows in Docker or directly on your host, with Git/PR automation.

> **⚠️ Security Notice:** This container runs the selected agent without approval prompts. Use only in isolated environments with trusted repositories.

## Why CodeMate?

Tired of approving every single command when pair programming with AI? Yet hesitant to grant full bypass permissions on your local machine? Every GitHub interaction requiring manual confirmation breaks your flow.

CodeMate solves this by running Claude Code in an isolated Docker container where it can operate freely without compromising your system. True pair programming starts here—let Claude focus on coding while you focus on the bigger picture.

## Features

- Automated repository cloning and PR management
- Pre-installed: Go, Node.js, Python, Rust, uv
- zsh with Oh My Zsh
- Persistent home configuration through `CODEMATE_HOME` (default `~/.codemate`)
- Built-in Claude Code skills for PR workflow automation
- Slack and Lark notifications when Claude stops (via `SLACK_WEBHOOK` / `LARK_WEBHOOK`)
- Direct Claude/Codex session launch with native initial-prompt support and Stop-hook PR monitoring

## Quick Start

### Prerequisites

- Docker
- GitHub CLI (`gh`) authenticated
- Anthropic API key

Run `codemate --setup` to create the required configuration files (global config in `CODEMATE_HOME`, default `~/.codemate/`, and project `.env`).

> **Note:** `git` is also required as a prerequisite; the CLI checks for it at startup.

#### Mac Users

On macOS, you need a Docker runtime since Docker doesn't run natively. Choose one:

- **[Docker Desktop](https://www.docker.com/products/docker-desktop/)** - Official Docker GUI application
- **[Colima](https://github.com/abiosoft/colima)** - Lightweight Docker runtime (recommended for CLI users)

### Installation

#### Global Installation (Recommended)

Install or upgrade the Python CLI globally with `uv`:

```bash
# Recommended
uv tool install --upgrade git+https://github.com/BoringHappy/CodeMate.git
```

If CodeMate is already installed, you can also upgrade it by package name:

```bash
uv tool upgrade codemate-cli
```

Uninstall the CLI with:

```bash
uv tool uninstall codemate-cli
```

If you prefer `pipx`, install with:

```bash
pipx install git+https://github.com/BoringHappy/CodeMate.git
```

Then run the one-time global setup:

```bash
# One-time global setup
codemate --setup
```

The `codemate` command is provided by the Python CLI package in `src/`.

### Usage

#### Host mode (macOS / Linux)

Run the installed host agent from a local Git checkout, without Docker or
`codemate --setup`. Codex is the default agent:

```bash
codemate --host --branch feature/my-task
codemate --host --branch feature/my-task --agent claude --query "Implement the requested change"
codemate --host --pr 123
codemate --host --issue 456
codemate --host --branch feature/local --no-pr
codemate --host --branch feature/chat --chat
codemate --host --branch feature/my-task --base-branch master --dry-run
codemate --xcode --branch feature/ios-task
codemate --xcode --pr 123
codemate worktree clean
```

`codemate worktree clean` runs directly on the host and opens an interactive
worktree selector. It lists the current repository's worktrees plus CodeMate
host worktrees under `${CODEMATE_HOME:-~/.codemate}/host/worktrees/`, so it also
works outside a checkout. Use **↑/↓** (or **j/k**) to select a row,
**Backspace** to request deletion, and **Enter** to confirm. **Esc** cancels
the confirmation; **R** refreshes the list; **Q** exits. Keyboard hints stay
at the bottom. The list shows each worktree's branch, path, and cleanup status.
Removal uses `git worktree remove` and retains branches. Primary checkouts,
the worktree containing the current directory, Git-locked worktrees, active
CodeMate sessions, and worktrees with uncommitted/untracked changes cannot be
removed. No Docker, agent, or GitHub authentication is needed.

On macOS, `--xcode` implies `--host` and opens the prepared worktree's Xcode
project before launching the agent. It also works with an explicit `--host`,
`--issue`, `--no-pr`, or `--chat`. CodeMate selects a directory ending in
`.xcworkspace` at the worktree root, falling back to `.xcodeproj` only when no
workspace exists. If none or multiple candidates of the preferred type exist,
startup stops with an error (listing ambiguous paths). The worktree is retained
for retry. `--dry-run` previews the opening step; `--config` reports the option;
neither opens Xcode. On other platforms, `--xcode` is ignored and the usual
host/container workflow runs.

Install and sign in to the selected agent locally first. Host mode requires
Git, bash, jq, and (for PR automation) an authenticated GitHub CLI. It retains
native agent credentials and model/MCP configuration. Codex launches with
`--yolo --no-daemon --no-alt-screen`, bypassing approval prompts and sandboxing;
Claude retains its native permission settings. Review and trust
the host plugin hooks when Codex asks before relying on PR monitoring.

Specify exactly one target: `--branch` for a new task, `--pr` for an open PR,
or `--issue` to use `issue-NUMBER`. The base must be `main` or `master`;
CodeMate prefers `main`, then `master`, using available upstream/origin refs
before local branches. `--base-branch` selects either explicitly; for an
existing PR it must match that PR's base. Fetch your base branch first if no
local ref is available.

CodeMate creates or reuses a linked Git worktree under
`${CODEMATE_HOME:-~/.codemate}/host/worktrees/<repository>/<branch>/`, then
launches the agent **in that worktree**. The original checkout and its uncommitted
changes remain intact. The task branch cannot be main/master or be checked out
in the primary checkout. Existing linked worktrees are reused and retained on
exit; PR automation requires the target worktree to be clean at launch.
A lock under the shared Git directory (`codemate-host-locks/`) prevents concurrent
CodeMate sessions on the same repository/branch, including sessions with different
`CODEMATE_HOME` values. The agent inherits the lock so it remains protected if
its launcher is killed. Different task branches can run concurrently. Locks do not prevent other
editors or plain agent sessions from changing your files.

Before creating or reusing the worktree, CodeMate fetches the latest selected
`main`/`master` from upstream/origin, including with `--no-pr` and `--chat`.
New task branches start from that updated remote base; the original checkout
and existing task branches are preserved. A failed fetch stops startup before
checkout. Repositories without either remote use the local base branch.
Before launching the agent, CodeMate finds an existing open PR for the task,
or creates a draft PR against the chosen base. For a new PR it adds an initial
empty commit if needed, pushes to origin,
uses the repository PR template and optional `--pr-title`, and records the PR
for monitoring. Forks with an upstream remote create the PR in upstream.
A failed setup stops before agent launch; retries reuse the worktree and PR.
Failed PR checkouts can resume in the registered detached worktree if it has
no uncommitted changes or commits beyond the base. Otherwise startup asks you
to preserve that work first. GitHub CLI failures include the underlying error.
The shared default system prompt plus host workflow instructions tell the agent
to read PR context, commit/push completed changes, and monitor feedback and CI.
`--no-pr` skips startup push/PR creation and commits locally. `--chat` also skips
workflow prompt injection and Stop automation. No project setup commands or
services are run.

Plugin resources ship in the CLI package. CodeMate publishes immutable bundles
under `${CODEMATE_HOME:-~/.codemate}/host/plugins/` and shares worktree-scoped
runtime state under `host/runtime/` (or `CODEMATE_RUNTIME_DIR`). Each launch has
a unique instance ID. Codex receives a private marketplace and plugin enablement
through process arguments; Claude receives session-only `--plugin-dir` paths.
CodeMate does not register or enable these plugins in global agent config, so
ordinary `codex`/`claude` launches do not load this host bundle. Previously
installed, globally enabled CodeMate plugins remain an explicit user setting:
disable those separately if you want ordinary launches to omit them. Host
launches disable the legacy `@codemate` plugins for their own session to avoid
duplicate hooks.

Container `.env` and setup configuration are not automatically imported.
The host process inherits your environment; use `--env KEY=VALUE` or
`--env-file path` for explicit additions. Container flags such as `--mount`,
`--repo`, `--build`, `--shell`, and `--pure` cannot be combined with `--host`.
`--dry-run` and `--config` do not install plugins or write launch state.

#### Basic Commands

```bash
# First time setup - creates global config and project .env
codemate --setup

# Run with explicit repo URL
codemate --repo https://github.com/your-org/your-repo.git --branch feature/xyz

# Run with branch name (auto-detects repo from: --repo > .env > current directory's git remote)
codemate --branch feature/your-branch

# Run Claude instead of the default Codex runtime
codemate --branch feature/your-branch --agent claude

# Run with a custom PR title
codemate --branch feature/your-branch --pr-title "My feature title"

# Run with existing PR
codemate --pr 123

# Run with GitHub issue (creates branch issue-NUMBER)
codemate --issue 456

# Fork-based workflow (for open-source contributions)
codemate --repo https://github.com/yourname/project.git --upstream https://github.com/maintainer/project.git --branch fix-bug
codemate --repo https://github.com/yourname/project.git --upstream https://github.com/maintainer/project.git --issue 789

# Skip PR creation on new branches (useful for forks or draft work)
codemate --branch feature/xyz --no-pr

# Chat mode skips PR creation and CodeMate system prompt injection
codemate --branch feature/xyz --chat

# Open an interactive zsh shell in the container instead of launching the agent
codemate --branch feature/xyz --shell

# Pure mode: plain zsh container with the local CodeMate home and current directory
# mounted, no repository setup, no GitHub, no plugins
codemate --pure

# Run with custom volume mounts (optional)
codemate --branch feature/xyz --mount ~/data:/data

# Run with initial query to Claude
codemate --branch feature/xyz --query "Please review the code and fix any issues"

# Build and run from local Dockerfile
codemate --build --branch feature/xyz

# Build with custom Dockerfile path and tag
codemate --build -f ./custom/Dockerfile --tag my-codemate:v1 --branch feature/xyz

# For Chinese users: serve the built-in images from a mirror registry
codemate --branch feature/xyz --image-registry ghcr.m.daocloud.io

# Skip pulling the image on startup (use the local image if present)
codemate --branch feature/xyz --skip-pull

# Pass arbitrary Docker run parameters (e.g. enable GPU access)
codemate --branch feature/xyz --docker-param "--gpus all"

# Attach the container to a specific Docker network (defaults to host on Linux)
codemate --branch feature/xyz --network bridge

# Run the container in a specific timezone (defaults to UTC)
codemate --branch feature/xyz --tz America/New_York
```

The setup command will:
1. Create global configuration in `CODEMATE_HOME` (default `~/.codemate/`; Claude config and settings)
2. Create project-specific `.env` file in your current directory
3. Prompt you for Anthropic API token and other settings

**Configuration Structure:**
- **Global config**: `CODEMATE_HOME` (default `~/.codemate/`) - shared home state; each top-level file or directory is mounted into `$HOME` with the same basename. Override the location with the `CODEMATE_HOME` environment variable (e.g., `export CODEMATE_HOME=/data/codemate`) to keep it anywhere on disk, not bound to `~/.codemate`
- **Project config**: `.env` in each project directory - Project-specific secrets and settings

**Repository URL Resolution**: The CLI determines the repository URL in this priority order:
1. `--repo` command-line argument (highest priority)
2. `CODEMATE_GIT_REPO_URL` environment variable or `.env` file
3. Current directory's git remote origin URL (auto-detected)
4. If none are available, an error is raised

##### Codex Terminal Mode

The `--agent codex` launcher (including `--chat`) runs `codex --yolo --no-daemon --no-alt-screen`:

- `--no-daemon` explicitly selects embedded mode. CodeMate injects workflow instructions with `--config developer_instructions=...`, which requires embedded mode; this avoids the `Running without the shared background server: command-line configuration overrides ... requires embedded mode` fallback notice.
- `--no-alt-screen` uses inline mode with native terminal scrollback. Codex CLI 0.157.0 enabled fullscreen transcripts and automatic background-server startup by default for eligible sessions; these are separate features, not an extra container or tmux session. See the [official OpenAI changelog](https://learn.chatgpt.com/docs/changelog).

When starting Codex manually from `--shell` or `--pure`, use `codex --yolo --no-daemon --no-alt-screen` for the same behavior. Docker detach and reattach remain available through `Ctrl+P Ctrl+Q` and re-running `codemate`.

##### Custom Volume Mounts

Use `--mount <host-path>:<container-path>` to mount additional directories or files. Useful for sharing data, configurations, or credentials with the container. Multiple `--mount` options can be specified.

##### Pure Mode

`codemate --pure` runs the pure image (`ghcr.io/boringhappy/codemate-pure:latest`): a plain zsh container with Claude Code and Codex installed, without repository setup, GitHub authentication, plugins, prompts, or PR monitoring.

```bash
# Start zsh with the local CodeMate home and the current directory mounted
codemate --pure

# Build the pure image locally and run it (defaults: -f docker/Dockerfile.pure --tag codemate-pure:local)
codemate --pure --build

# Combine with the regular Docker options
codemate --pure --network bridge --mount ~/data:/data --tz America/New_York
```

What pure mode does:

- Mounts each top-level entry of its own home, `CODEMATE_PURE_HOME` (default `~/.codemate-pure`), at the matching path in `$HOME` (`.claude`, `.claude.json`, `.codex`). The pure home itself is not mounted, and it is created on first run and seeded with those entries, so logging in inside the container persists there
- Mounts the current working directory at the path it has relative to your home directory (`~/code/projecta` → `/home/agent/code/projecta`) and starts there; directories outside the home fall back to `/home/agent/<directory-name>`
- Runs the image's default command, `zsh`
- Aliases `claude` and `codex` in `~/.zshrc` to their allow-all-permissions flags (`claude --dangerously-skip-permissions`, `codex --yolo`), so agents started from the shell skip approval prompts like the CodeMate launchers do
- Keeps `--mount`, `--docker-param`, `--network`, `--image`, `--env`, `--env-file`, `--tz`, `--skip-pull`, and `--dry-run` available

What pure mode skips:

- No `--branch`, `--pr`, or `--issue` target is required; target values from `.env`, the environment, or the command line are ignored
- No GitHub token, git identity, or repository URL is required
- Only `docker` must be installed on the host (`git` and `gh` are not required)
- `--query` and `--shell` are rejected: the container already opens zsh, and there is no agent launcher to receive a query

Notes:

- Pure mode never touches the standard `CODEMATE_HOME` (`~/.codemate`): the two homes are separate paths, so pure sessions have their own Claude/Codex credentials and settings. Set `CODEMATE_PURE_HOME` to move it elsewhere; it defaults to the standard home with a `-pure` suffix, so `CODEMATE_HOME=/data/codemate` implies `/data/codemate-pure`
- `--image` and `--build` select the image in pure mode; `CODEMATE_IMAGE` from `.env` or the environment is ignored so a standard image setting does not leak into pure mode. The pure default follows `CODEMATE_IMAGE_REGISTRY`, just like the standard default
- Use `--network <mode>` for Docker network modes. With `--docker-param`, keep a flag and its value in one quoted string: `--docker-param "--network bridge"` works, `--docker-param --network bridge` does not

##### Building from Local Dockerfile

For development or customization, you can build CodeMate from a local Dockerfile:

```bash
# Build from the default Claude Dockerfile
codemate --build --branch feature/xyz

# Build from custom Dockerfile path
codemate --build -f ./path/to/Dockerfile --branch feature/xyz

# Build with custom image tag
codemate --build --tag my-codemate:dev --branch feature/xyz

# Combine all options
codemate --build -f ./custom/Dockerfile --tag my-codemate:v1 --branch feature/xyz
```

**Options:**
- `--build` - Build Docker image from local Dockerfile before running
- `-f, --dockerfile PATH` - Path to Dockerfile (default: `docker/Dockerfile`, or `docker/Dockerfile.pure` with `--pure`)
- `--tag TAG` - Image tag for local build (default: `codemate:local`, or `codemate-pure:local` with `--pure`)
  - **Note:** Only works with `--build`. To use a pre-built image, use `--image` instead

When `--build` is used:
1. The CLI builds the Docker image from the specified Dockerfile
2. The default image tag is `codemate:local` (unless `--tag` is specified)
3. The locally built image is used instead of pulling from the registry
4. The `--image` option is ignored when `--build` is used

**Adding Custom Toolchains:**

To add additional toolchains or tools to the container, create a custom Dockerfile that extends the base image:

```dockerfile
# Custom Dockerfile with additional toolchains
FROM ghcr.io/boringhappy/codemate:latest

# Add Java
RUN apt-get update && apt-get install -y openjdk-17-jdk maven

# Add PHP
RUN apt-get install -y php php-cli php-mbstring composer

# Add Ruby
RUN apt-get install -y ruby-full
RUN gem install bundler

# Add any other tools you need
RUN apt-get install -y postgresql-client redis-tools

# Clean up
RUN apt-get clean && rm -rf /var/lib/apt/lists/*
```

Then build and run with your custom Dockerfile:

```bash
codemate --build -f ./Dockerfile.custom --tag codemate:custom --branch feature/xyz
```

**Preinstalled Browser Automation:**

The base image ships the [Playwright](https://playwright.dev/) CLI and Chromium system dependencies, so Chromium needs no additional OS packages. Browsers are not bundled — download the ones your project needs on demand:

```bash
playwright --version
playwright install chromium                  # add firefox / webkit as needed
playwright screenshot "https://example.com" /tmp/page.png
npx playwright test
```

Browsers are downloaded into the current user's cache (`~/.cache/ms-playwright`) and need no elevated privileges. For Firefox or WebKit, install their additional system dependencies as needed:

```bash
sudo "$(command -v playwright)" install-deps firefox webkit
```

## Environment Variables

> **Note:** When using `codemate`, these variables are handled automatically through the setup process. This reference is primarily for advanced Docker usage or troubleshooting.

The `codemate` CLI resolves configuration in this order:

1. Command-line options, such as `--repo`, `--branch`, `--agent`, `--mount`, and `--docker-param`
2. Project `.env`
3. Ambient shell environment variables
4. Command-derived values and built-in defaults, such as `git config user.name`, `gh auth token`, and the current repo remote

Docker receives generated environment values from that resolved configuration; the project `.env` file is not passed through directly.

| Variable | Required | Description |
|----------|----------|-------------|
| `CODEMATE_GIT_REPO_URL` | No | Repository URL (defaults to current repo's remote) |
| `CODEMATE_UPSTREAM_REPO_URL` | No | Upstream repository URL (for fork-based workflows) |
| `CODEMATE_GITHUB_TOKEN` | Auto | GitHub personal access token (defaults to `gh auth token` if not provided) |
| `CODEMATE_GIT_USER_NAME` | Auto | Git commit author name (defaults to `git config user.name` if not provided) |
| `CODEMATE_GIT_USER_EMAIL` | Auto | Git commit author email (defaults to `git config user.email` if not provided) |
| `CODEMATE_CO_AUTHOR_BY` | No | Commit co-author used by the Git commit skill, e.g. `Name <email@example.com>` or `Co-authored-by: Name <email@example.com>` |
| `CODEMATE_IMAGE` | No | Custom image (default: `ghcr.io/boringhappy/codemate:latest`) |
| `CODEMATE_IMAGE_REGISTRY` | No | Registry serving the built-in images (default: `ghcr.io`). Set it to pull `codemate`/`codemate-pure` from a mirror without passing `--image`; an explicit `--image`, `CODEMATE_IMAGE`, or `--build` tag is never rewritten |
| `CODEMATE_SKIP_PULL` | No | Skip pulling the Docker image at startup; the image is only pulled if it is missing locally |
| `CODEMATE_HOME` | No | CodeMate home directory on the host; supports `~` and `$VAR` expansion (default: `~/.codemate`) |
| `CODEMATE_PURE_HOME` | No | Home directory used by `--pure` sessions; supports `~` and `$VAR` expansion (default: the `CODEMATE_HOME` path with a `-pure` suffix, e.g. `~/.codemate-pure`) |
| `CODEMATE_AGENT` | No | Runtime to launch: `codex` (default) or `claude` |
| `CODEMATE_INSTANCE_ID` | No | Runtime instance namespace used to distinguish concurrent agent processes |
| `CODEMATE_RUNTIME_DIR` | No | Override the root for session-scoped hook state (defaults to `$XDG_RUNTIME_DIR/codemate` or `/tmp/codemate-<uid>`) |
| `CODEMATE_TMPDIR` | No | Per-agent temp root written into the container env (`/home/agent/.claude/tmp` for Claude, `/home/agent/.codex/tmp` for Codex); hooks derive their runtime root from it when `CODEMATE_RUNTIME_DIR` is unset |
| `CODEMATE_NO_PR` | No | Skip PR creation and branch push |
| `CODEMATE_CHAT` | No | Chat mode; derives `CODEMATE_NO_PR=true` and skips CodeMate system prompt injection |
| `TZ` | No | Container timezone (default: `UTC`; override with `--tz`, `.env`, or the ambient environment) |
| `SLACK_WEBHOOK` | No | Slack Incoming Webhook URL for notifications when Claude stops (only sent if new commits exist) |
| `LARK_WEBHOOK` | No | Lark Incoming Webhook URL for notifications when Claude stops (only sent if new commits exist) |
| `ANTHROPIC_AUTH_TOKEN` | No | Anthropic API token (for custom API endpoints) |
| `ANTHROPIC_BASE_URL` | No | Anthropic API base URL (for custom API endpoints) |
| `CODEMATE_DEFAULT_MARKETPLACES` | No | Comma-separated default plugin marketplaces (default: `BoringHappy/CodeMate`) |
| `CODEMATE_DEFAULT_PLUGINS` | No | Comma-separated default plugins (default: `git@codemate,pr@codemate,dev@codemate,issue@codemate,workspace@codemate`) |
| `CODEMATE_CUSTOM_MARKETPLACES` | No | Comma-separated list of custom plugin marketplace repositories (e.g., `username/repo1,org/repo2`) |
| `CODEMATE_CUSTOM_PLUGINS` | No | Comma-separated list of custom plugins to install (e.g., `plugin1@marketplace1,plugin2@marketplace2`) |
| `CODEMATE_SOFT_LINKS` | No | Comma-separated `source:destination` pairs to symlink after repo setup (e.g., `/data/models:/home/agent/models,/data/cache:/home/agent/.cache`) |

`CODEMATE_BRANCH_NAME`, `CODEMATE_PR_NUMBER`, `CODEMATE_PR_TITLE`, `CODEMATE_ISSUE_NUMBER`, `CODEMATE_QUERY`, `CODEMATE_NO_PR`, `CODEMATE_CHAT`, `CODEMATE_SKIP_PULL`, and `CODEMATE_CO_AUTHOR_BY` can be set through CLI options, `.env`, or ambient environment variables. Prefer CLI options for one-off runs. Use `codemate --agent claude|codex` to override `CODEMATE_AGENT` from `.env` for a single run, `codemate --chat` to skip PR creation and CodeMate system prompt injection, `codemate --shell` to run the same container setup and then drop into an interactive zsh shell instead of starting the agent, `codemate --pure` to skip setup entirely and run a plain zsh container with its own home (`CODEMATE_PURE_HOME`) mounted, `codemate --skip-pull` to use a locally cached Docker image without forcing a pull on startup, and `codemate --co-author-by "Name <email@example.com>"` to add a co-author for commits made by the Git commit skill.


## Automatic Project Services

Commit `.codemate/config.yaml` in your repository to install dependencies and
start development services when a standard CodeMate container starts. This also
works with `--shell` and `--chat`; `--pure` does not run project setup.

Use the `workspace:setup-services` skill in Codex or
`/workspace:setup-services` in Claude Code to create or update this file for
your repository. The skill inspects existing development commands and lockfiles,
then configures dependency setup, services, and readiness checks.

This repository includes a [minimal HTTP service example](examples/empty-service/README.md)
on port 8000, configured in [`.codemate/config.yaml`](.codemate/config.yaml),
with no extra dependencies.

```yaml
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
  web:
    cwd: frontend
    command: npm run dev -- --host 0.0.0.0 --port 3000 --strictPort
    ready:
      http: http://127.0.0.1:3000/
```

- `version: 1` is optional. `setup` and `services` are both optional; without
  the file, startup follows the existing behavior.
- `cwd` defaults to the repository root and must stay inside it. Commands run
  in Bash and inherit the container environment, including existing `.env` /
  `--env` values. Keep service commands in the foreground (no `&` or daemon mode).
- Setup runs sequentially on each container start, with a default timeout of
  300 seconds per step; override with `timeout: 600` on a step. Setup commands
  should be idempotent. A failed step skips remaining setup and service startup.
- Services start together under Supervisor. `ready.http` is optional and waits
  for HTTP 2xx, with a default timeout of 30 seconds; override with
  `ready.timeout: 60`. Without a probe, a process must stay alive for one second.
  Probes bypass HTTP proxy environment variables.
- Configuration/setup errors or failed readiness checks are reported, then the
  agent/shell still opens for repairs. A readiness timeout leaves the process
  running. Supervisor retries startup failures and restarts unexpected exits.
- `codemate-services up` runs setup, starts Supervisor in the background, waits
  for readiness, and returns. The container entrypoint then prints the session
  banner and directly executes Claude, Codex, or the shell. The service manager
  does not launch or wait for an agent.
- Re-attaching does not rerun setup or duplicate services. Supervisor and its
  services remain independent of agent sessions while the container is running;
  Ctrl+C in the agent does not stop them. Stopping the container ends its
  background processes as well.

Inside the container, from anywhere in the repository:

```bash
codemate-services up             # Initialize background services; no agent is started
codemate-services status
codemate-services status --json   # Live process and HTTP readiness status
codemate-services logs web --follow
codemate-services restart api
codemate-services stop web
codemate-services start web
```

Runtime state and rotating logs live under
`/tmp/codemate-services-<uid>-<workspace-hash>`, not in the repository or shared
CodeMate home. Setup output goes to `setup.log`; configuration/startup errors
go to `startup.log`. `status --json` includes the log locations. Recreating the
container removes these logs. Configuration changes take effect on the next
container start. If no Supervisor is running yet, `up` can initialize services
in an existing container; otherwise it reuses the running Supervisor without
rerunning setup. `start` and `restart` reuse the loaded service definitions.
`up` returns a nonzero exit code on setup or readiness failure; the container
entrypoint reports it and still opens the session for repairs.

The workspace plugin checks current service status on `SessionStart` and adds
the summary to both Claude and Codex context, including setup failures, process
state, HTTP readiness, and log locations. Resuming a session checks again;
hooks never start services or rerun setup.

This feature does not publish Docker ports or provide a preview proxy. For
bridge networking, publish the ports you need when creating the container,
for example `--network bridge --docker-param "-p 127.0.0.1:3000:3000"`.

Run the test suite locally with
`uv run --with pytest --with pyyaml --with supervisor pytest -q`.
The service integration tests run real Supervisor, HTTP services, and a PTY
without requiring a Docker daemon.

## How It Works

CodeMate uses a separate [base image (`codemate-base`)](https://github.com/BoringHappy/CodeMate/pkgs/container/codemate-base) that is rebuilt weekly to keep system packages and development tools up-to-date.

On startup, the container:
1. Configures git user from environment variables
2. Authenticates GitHub CLI with token
3. Clones/updates repository to `/home/agent/<repo-name>`
4. Checks out the specified branch or PR
5. Creates a draft PR if working on a new branch (unless `--no-pr`, `--chat`, or fork workflow)
6. Runs `.codemate/config.yaml` setup and starts project services, if configured
7. Installs/updates plugins for the selected agent from configured marketplaces
8. Starts Claude Code or Codex directly with the initial query as a native initial prompt, appending CodeMate instructions unless chat mode is enabled
9. Sends the initial query to the selected agent if `--query` is provided
10. Uses the workspace plugin's Stop hook to monitor PR comments, CI failures, and review-ready state while the agent is idle

With `--shell`, the container runs steps 1-6 and then opens an interactive zsh shell instead of installing agent plugins and starting Claude Code or Codex.

With `--pure`, none of those steps run. The pure image starts zsh directly with its own home (`CODEMATE_PURE_HOME`, default `~/.codemate-pure`) and the current directory mounted, so it needs no GitHub token, git identity, repository URL, or host `git`/`gh` installation. See [Pure Mode](#pure-mode) for details.

## Skills

[CodeMate](https://github.com/BoringHappy/CodeMate) comes with pre-installed skills automatically available when you start the container, providing workflow automation for Git, PR management, and more.

### Available Plugins

**Git Plugin** (`git@codemate`):
| Command | Description |
|---------|-------------|
| `/git:commit` | Stage all changes, create a commit with a meaningful message, and push to remote |

**PR Plugin** (`pr@codemate`):
| Command | Description |
|---------|-------------|
| `/pr:get-details` | Fetch PR information including title, description, file changes, and review comments |
| `/pr:create` | Create a pull request from the current branch; supports standard and fork workflows |
| `/pr:fix-comments` | Read PR review comments, fix the issues, commit changes, and reply to comments |
| `/pr:update` | Update PR title and/or summary. Use `--skip-title` to update only the summary |
| `/pr:ack-comments` | Acknowledge PR issue comments by adding 👀 reaction |

**Issue Plugin** (`issue@codemate`):
| Command | Description |
|---------|-------------|
| `/issue:read-issue` | Read GitHub issue details including title, description, labels, and comments |
| `/issue:refine-issue` | Rewrite issue body to match template (plan-then-execute, requires approval) |
| `/issue:triage-issue` | Apply priority and category labels based on content analysis |
| `/issue:classify-issue` | Post clarifying questions for ambiguous issues and add `needs-more-info` label |

**PM Plugin** (`pm@codemate`) — _recommended for local Claude Code, not bundled in the Docker image. Install via `claude plugin install pm@codemate` or add to `CODEMATE_CUSTOM_PLUGINS` if you want it inside the container._
| Command | Description |
|---------|-------------|
| `/pm:spec-list` | List all spec GitHub Issues with their status and task counts |
| `/pm:spec-init <name>` | Start a guided brainstorming session to create a new spec as a GitHub Issue |
| `/pm:spec-plan <issue-number> [--granularity micro\|pr\|macro]` | Post a technical implementation plan as a comment on the spec issue; user must 👍 the comment to approve before decomposing |
| `/pm:spec-decompose <issue-number> [--granularity micro\|pr\|macro]` | Create task sub-issues from the approved plan comment; requires 👍 reaction on the plan comment |
| `/pm:spec-status <issue-number>` | Show live progress summary from GitHub Issues |
| `/pm:spec-next <issue-number>` | Find the next actionable task based on dependencies |
| `/pm:spec-done <issue-number>` | Summarize changes, post a done comment, close the spec issue, and add `done` label |
| `/pm:spec-abandon <issue-number>` | Close the spec issue and optionally its linked task issues |

The `--granularity` flag controls task sizing:
- `micro` — 0.5–1 day tasks (fine-grained, commit-level)
- `pr` — 1–3 day tasks, ~200–400 LOC per PR (default)
- `macro` — 3–7 day milestones / epics

**Workspace Plugin** (`workspace@codemate`):
| Command | Description |
|---------|-------------|
| `/workspace:best-practice` | Bootstrap a repo with spec issue templates, labels, and PR template |
| `/workspace:setup-services` | Create or update `.codemate/config.yaml` for automatic dependency setup and project service startup |

The workspace plugin also installs session lifecycle hooks:
- **SessionStart** — records session start time and current commit in session-scoped runtime state
- **UserPromptSubmit** — marks that specific session active
- **Stop** — checks for uncommitted changes, sends Slack/Lark notifications, and monitors the current branch's PR

### Custom Plugins

You can extend CodeMate with your own custom plugins by adding them to your `.env` file:

```bash
# Override default marketplaces (optional)
CODEMATE_DEFAULT_MARKETPLACES=BoringHappy/CodeMate

# Override default plugins (optional)
CODEMATE_DEFAULT_PLUGINS=git@codemate,pr@codemate,dev@codemate,issue@codemate,workspace@codemate

# Set to empty to disable all defaults (optional)
CODEMATE_DEFAULT_MARKETPLACES=
CODEMATE_DEFAULT_PLUGINS=

# Add custom plugin marketplaces (comma-separated GitHub repo paths)
CODEMATE_CUSTOM_MARKETPLACES=username/my-marketplace,org/another-marketplace

# Add custom plugins to install (comma-separated plugin names)
CODEMATE_CUSTOM_PLUGINS=my-plugin@my-marketplace,another-plugin@my-marketplace
```

**How it works:**
1. By default, CodeMate installs marketplaces from `CODEMATE_DEFAULT_MARKETPLACES` and plugins from `CODEMATE_DEFAULT_PLUGINS`
2. You can override these defaults by setting the environment variables to different values
3. You can disable all defaults by setting them to empty strings
4. Custom marketplaces and plugins are added after defaults during container startup
5. All plugins become available as skills (e.g., `/my-plugin:command`)
6. The setup is idempotent - already installed plugins are skipped

**Example:**

If you have a custom plugin marketplace at `github.com/myorg/my-plugins` with a plugin called `deploy`, you would configure:

```bash
CODEMATE_CUSTOM_MARKETPLACES=myorg/my-plugins
CODEMATE_CUSTOM_PLUGINS=deploy@my-plugins
```

Then use it in Claude Code:
```bash
/deploy:production
```

## Issue-Based Workflow

CodeMate supports starting work directly from a GitHub issue using the `--issue` flag. This workflow automatically:

1. Creates a branch named `issue-{NUMBER}` (or uses existing branch if it already exists)
2. Sends an initial query to Claude to read and address the issue using `/issue:read-issue` skill
3. Claude analyzes the issue details (title, description, labels, comments)
4. Claude implements the requested changes
5. Creates a PR when you're ready to commit

**Example:**

```bash
# Start working on issue #456
codemate --issue 456
```

This is equivalent to:
```bash
codemate --branch issue-456 --query "Please use /issue:read-issue skill to read and address issue #456"
```

**When to use:**
- Starting new work from a GitHub issue
- Implementing feature requests tracked as issues
- Fixing bugs documented in issues

## PR Comment Monitoring

Host sessions (`codemate --host`) check PR feedback once per Stop for both
Codex and Claude, without backoff or retries. If another session holds the
branch monitor lock, the check is skipped. Feedback found during the check
still triggers a continuation; later feedback is checked at the next Stop.
The polling behavior below applies to container sessions.

CodeMate monitors PR feedback from the workspace plugin's native `Stop` hook. The first check runs immediately; later checks back off to 10, 30, 60, and then at most 120 seconds, up to 30 checks per invocation. No cron daemon or tmux prompt injection is used. Claude runs the poller as an `asyncRewake` hook so the UI remains interactive; Codex uses its synchronous Stop continuation contract to start a native agent turn when feedback arrives.

Codex keeps monitoring through this polling window instead of yielding after five seconds. `CODEMATE_MONITOR_MAX_SECONDS` can set an optional time limit; the default `0` disables it. Submitted prompts and persistent Codex queue entries end monitoring; CLI Tab messages held in the TUI do not.

The hook verifies that its own session is still stopped and that the current worktree/branch still has an open PR before every `gh` call. It also watches the agent's prompt history (`$CODEX_HOME/history.jsonl` for Codex, `$CLAUDE_CONFIG_DIR/history.jsonl` for Claude): a new user prompt is recorded there the moment it is submitted, so even while Codex is still blocked running the Stop hook, the in-flight monitor notices within a second and exits, letting the new message resume the session without pressing Esc. When feedback is found, Claude is awakened through `asyncRewake`; Codex receives a structured Stop continuation decision. Both paths create a native agent turn.

### State Isolation

- Session status is keyed by runtime instance, agent, and `session_id`; notification commit baselines and retry counters are partitioned again by Git worktree and branch.
- PR state is resolved live from GitHub (source of truth) through the `pr` plugin's query-first `pr-status` interface, so no shared PR-status file exists between plugins. The pr plugin keeps only a private runtime-root cache for disambiguation; workspace monitor-state and lock files hold shared cursors and an interruptible branch lease under the runtime root (never inside `.git`), so only one stopped session handles a given PR event.
- Docker container names include the runtime agent (`codemate-<agent>-<repo>-<branch>`), so Claude and Codex sessions for the same repository/branch can run concurrently on one machine without attaching to each other's container.
- Each runtime keeps its own writable state under its own config directory: `CODEMATE_TMPDIR` and the derived hook runtime root are `/home/agent/.claude/tmp` for Claude and `/home/agent/.codex/tmp` for Codex, so temp files and session state never share a location across runtimes. CodeMate never overrides the global `TMPDIR`, which would affect every process in the container.
- Fixed shared files such as `/tmp/.session_status`, `/tmp/.pr_status`, and `/tmp/pr-monitor-state` are not used.

### What Gets Monitored

Each poll checks the following in priority order (only one continuation is created per poll):

1. **CI failures** — if a CI check fails on the current branch, the selected agent receives the failure logs and is asked to fix them
2. **PR ready for review** — when a draft PR is marked ready for review, the selected agent is asked to update the PR title and description via `pr:update` (which also applies the `pr-updated` label so the PR isn't re-notified)
3. **Issue comments** — new general PR comments (Conversation tab) without a 👀 reaction are forwarded to the selected agent
4. **Review comments** — unresolved inline code comments (Files changed tab) trigger `/pr:fix-comments`

### Comment Types

GitHub PRs have two types of comments that CodeMate monitors:

| Type | Location | API Endpoint | Use Case |
|------|----------|--------------|----------|
| **Review Comments** | Files changed tab (inline) | `/pulls/{pr}/comments` | Code-specific feedback on particular lines |
| **Issue Comments** | Conversation tab | `/issues/{pr}/comments` | General discussion, questions, requests |

### Review Comments Workflow

When someone leaves a **review comment** (inline code comment):

1. Monitor detects unresolved review comments
2. Continues the selected agent with a request to use `pr:fix-comments`
3. The agent uses the workflow to:
   - Read the feedback
   - Make code changes
   - Commit and push
   - Reply with "CodeMate Replied: ..." to mark as resolved

### Issue Comments Workflow

When someone leaves an **issue comment** (general PR comment):

1. Monitor detects new issue comments without 👀 reaction
2. Continues the selected agent with the actual comment content
3. The agent processes the request
4. The agent uses `pr:ack-comments` to add a 👀 reaction
5. Future runs skip comments with 👀 reaction

### Filtering Logic

Comments are filtered out if they:
- Are posted by bots (login ending in `[bot]`)
- Start with "CodeMate Replied:" (already handled)
- Have 👀 reaction (already acknowledged)

## Best Practices

### Add a Pull Request Template

Create `.github/PULL_REQUEST_TEMPLATE.md` in your target repository to standardize PR descriptions:

```markdown
## Summary
<!-- Brief description of changes -->

## Test Plan
<!-- How to verify the changes -->

## Checklist
- [ ] Tests added/updated
- [ ] Documentation updated
```

### Security Recommendations

- Run CodeMate only on trusted repositories
- Use short-lived GitHub tokens with minimal scopes
- Avoid mounting sensitive host directories
- Review changes before merging PRs created by Claude

## License

MIT
