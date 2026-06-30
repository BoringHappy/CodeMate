# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

CodeMate is a Docker-based environment for running Claude Code or Codex with automated Git/PR setup. The merged image selects its runtime from `CODEMATE_AGENT` and runs it without approval prompts inside an isolated container.

## Running CodeMate

```bash
# Run with branch name (auto-detects repo from: --repo > .env > current directory's git remote)
codemate --branch feature/your-branch

# Run with explicit repo URL
codemate --repo https://github.com/your-org/your-repo.git --branch feature/xyz

# Run with existing PR
codemate --pr 123

# Run with custom volume mounts
codemate --branch feature/xyz --mount /local/path:/container/path
```

Parameters:
- `--repo` - Repository URL (optional, auto-detects from .env or git remote)
- `--branch` - Branch to work on
- `--pr` - Existing PR number (alternative to --branch)
- `--mount` - Additional volume mounts (can be specified multiple times)

## Architecture

### Container Startup Flow

1. The combined image uses `setup/setup.sh` for shared Git, GitHub, repository, pre-commit, and soft-link initialization.
2. `setup/shell/setup-git.sh` configures git user from environment variables
3. `setup/shell/setup-gh.sh` authenticates GitHub CLI with token
4. `setup/python/setup-repo.py` clones repo, checks out branch/PR, creates PR if needed
5. `setup/shell/setup-precommit.sh` installs pre-commit git hooks when the cloned repo contains a `.pre-commit-config.yaml` (skips silently otherwise)
6. `setup/setup.sh` enforces the region restriction before any other setup. `CODEMATE_ALLOW_IP` takes precedence over `CODEMATE_ALLOW_COUNTRY`; failed checks file a GitHub issue and stop startup.
7. `setup/run.sh` starts the PR-monitor cron daemon and dispatches by `CODEMATE_AGENT`. `setup/run-claude.sh` performs ccline and Claude plugin setup; `setup/run-codex.sh` installs Codex plugins through `setup/shell/setup-codex-plugins.sh`.

Note: All setup scripts live under `docker/setup/` in the repository, but are copied to `/usr/local/bin/setup/` inside the container.

### Required Environment Variables

- `CODEMATE_ALLOW_COUNTRY` — comma-separated ip-api.com `countryCode` values (e.g. `US,CA`).
- `CODEMATE_ALLOW_IP` — comma-separated exact IPs or IPv4 CIDR ranges (e.g. `203.0.113.7,198.51.100.0/24`).

At least **one** of `CODEMATE_ALLOW_COUNTRY` / `CODEMATE_ALLOW_IP` must be set — the launcher refuses to start the container otherwise. The container enforces the check at startup. `CODEMATE_ALLOW_IP` takes precedence: if it is set, only the IP allowlist is checked (and only `ifconfig.me` is called); `CODEMATE_ALLOW_COUNTRY` is consulted solely as a fallback when `CODEMATE_ALLOW_IP` is unset. This keeps the check to a single external API call.

### Plugin Marketplace

CodeMate uses the CodeMatePlugin marketplace to distribute plugins. Plugins are installed at runtime during container startup via `setup/shell/setup-plugins.sh` using the `claude plugin` CLI commands.

The marketplace is fetched from the external repository: `BoringHappy/CodeMatePlugin`

**Default Marketplaces:**
- `codemate` (BoringHappy/CodeMate) - CodeMate plugins

**Default Plugins:**

**Git Plugin** (`git@codemate`):
- `/git:commit` - Stage, commit, and push changes

**PR Plugin** (`pr@codemate`):
- `/pr:get-details` - Fetch PR information including comments
- `/pr:fix-comments` - Address PR review feedback
- `/pr:update` - Update PR title and summary

**Dev Plugin** (`dev@codemate`):
- `/dev:read-env-key` - List environment variable keys
- `/dev:run-image` - Run an existing container image as a Kubernetes pod with local files injected from the CodeMate workspace
- `/dev:manage-k8s` - Interact with Kubernetes clusters using kubectl and helm

**Issue Plugin** (`issue@codemate`):
- `/issue:read-issue` - Fetch issue details including comments
- `/issue:refine-issue` - Rewrite issue body to match template (plan-then-execute, requires approval)
- `/issue:triage-issue` - Apply priority and category labels based on content analysis
- `/issue:classify-issue` - Post clarifying questions for ambiguous issues and add `needs-more-info` label

**Workspace Plugin** (`workspace@codemate`):
- Session lifecycle hooks: tracks SessionStart, UserPromptSubmit, and Stop events to `/tmp/.session_status`
- Slack notification on Stop: sends a message to `SLACK_WEBHOOK` when new commits are pushed (requires `SLACK_WEBHOOK` env var)
- `/workspace:best-practice` - Bootstrap a repo with spec issue templates, labels, and PR template

**Configuring Default Plugins:**

You can customize which marketplaces and plugins are installed by default using environment variables in the `.env` file:

```bash
# Override default marketplaces (comma-separated GitHub repo paths)
CODEMATE_DEFAULT_MARKETPLACES=BoringHappy/CodeMate

# Override default plugins (comma-separated plugin@marketplace)
CODEMATE_DEFAULT_PLUGINS=git@codemate,pr@codemate,dev@codemate,issue@codemate,workspace@codemate

# Set to empty to disable all defaults
CODEMATE_DEFAULT_MARKETPLACES=
CODEMATE_DEFAULT_PLUGINS=
```

**Custom Plugins:**

You can add additional custom plugin marketplaces and plugins by configuring environment variables in the `.env` file:

```bash
# Add custom marketplaces (comma-separated GitHub repo paths)
CODEMATE_CUSTOM_MARKETPLACES=username/my-marketplace,org/another-marketplace

# Add custom plugins to install (comma-separated plugin names)
CODEMATE_CUSTOM_PLUGINS=my-plugin@my-marketplace,another-plugin@my-marketplace
```

Custom marketplaces and plugins are added/installed after the default ones during container startup. The setup script will automatically:
1. Add all default and custom marketplaces to Claude Code
2. Install all default and custom plugins from those marketplaces
3. Skip any that are already installed (idempotent)

### Key Files

- `docker/Dockerfile` - Combined Claude Code and Codex container definition, using the `codemate-base` image
- `docker/Dockerfile.base` - Base image with system packages and development tools
- `docker/setup/` - Container setup scripts (copied into container at build time)
- `codemate` - Main script to run CodeMate with configuration management (installed globally or run locally)
- `docker/setup/python/setup-repo.py` - Main repo/PR setup logic, reads PR template from `.github/PULL_REQUEST_TEMPLATE.md`

## Development Notes

- No test suite exists - this is infrastructure/tooling
- GitHub Actions workflow (`docker-build.yml`) builds and pushes the combined image to GHCR on main branch and tags
- GitHub Actions workflow (`docker-build-schedule.yml`) triggers a rebuild every day at 05:00 UTC
- Multi-platform builds: linux/amd64 and linux/arm64
