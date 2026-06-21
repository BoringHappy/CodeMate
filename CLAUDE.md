# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

CodeMate is a Docker-based environment for running Claude Code with automated Git/PR setup. It runs Claude with `--dangerously-skip-permissions` in an isolated container, enabling AI pair programming without constant approval prompts.

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

1. `setup/setup.sh` orchestrates initialization, running `setup/shell/check-region.sh` first; it detects the public IP from `https://ifconfig.me/ip` and the country/region from `http://ip-api.com/json/`. The check passes if the detected IP matches `CODEMATE_ALLOW_IP` **or** the detected country matches `CODEMATE_ALLOW_COUNTRY`. If neither matches it files a GitHub issue and exits before any other setup runs
2. `setup/shell/setup-git.sh` configures git user from environment variables
3. `setup/shell/setup-gh.sh` authenticates GitHub CLI with token
4. `setup/python/setup-repo.py` clones repo, checks out branch/PR, creates PR if needed
5. `setup/shell/setup-precommit.sh` installs pre-commit git hooks when the cloned repo contains a `.pre-commit-config.yaml` (skips silently otherwise)
6. `setup/run.sh` launches Claude Code with system prompt from `setup/prompt/system_prompt.txt`

Note: All setup scripts live under `docker/setup/` in the repository, but are copied to `/usr/local/bin/setup/` inside the container.

### Required Environment Variables

- `CODEMATE_ALLOW_COUNTRY` — comma-separated ip-api.com `countryCode` values (e.g. `US,CA`).
- `CODEMATE_ALLOW_IP` — comma-separated exact IPs or IPv4 CIDR ranges (e.g. `203.0.113.7,198.51.100.0/24`).

At least **one** of `CODEMATE_ALLOW_COUNTRY` / `CODEMATE_ALLOW_IP` must be set — the launcher refuses to start the container otherwise. The container enforces the check at startup and passes if **either** the detected IP or country is allowlisted.

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
DEFAULT_MARKETPLACES=BoringHappy/CodeMate

# Override default plugins (comma-separated plugin@marketplace)
DEFAULT_PLUGINS=git@codemate,pr@codemate,dev@codemate,issue@codemate,workspace@codemate

# Set to empty to disable all defaults
DEFAULT_MARKETPLACES=
DEFAULT_PLUGINS=
```

**Custom Plugins:**

You can add additional custom plugin marketplaces and plugins by configuring environment variables in the `.env` file:

```bash
# Add custom marketplaces (comma-separated GitHub repo paths)
CUSTOM_MARKETPLACES=username/my-marketplace,org/another-marketplace

# Add custom plugins to install (comma-separated plugin names)
CUSTOM_PLUGINS=my-plugin@my-marketplace,another-plugin@my-marketplace
```

Custom marketplaces and plugins are added/installed after the default ones during container startup. The setup script will automatically:
1. Add all default and custom marketplaces to Claude Code
2. Install all default and custom plugins from those marketplaces
3. Skip any that are already installed (idempotent)

### Key Files

- `docker/Dockerfile` - Main container definition, uses `codemate-base` image
- `docker/Dockerfile.base` - Base image with system packages and development tools
- `docker/Dockerfile.pure-claude` - Minimal Claude Code image
- `docker/setup/` - Container setup scripts (copied into container at build time)
- `codemate` - Main script to run CodeMate with configuration management (installed globally or run locally)
- `docker/setup/python/setup-repo.py` - Main repo/PR setup logic, reads PR template from `.github/PULL_REQUEST_TEMPLATE.md`

## Development Notes

- No test suite exists - this is infrastructure/tooling
- GitHub Actions workflow (`docker-build-push.yml`) builds and pushes to GHCR on main branch and tags
- Multi-platform builds: linux/amd64 and linux/arm64
