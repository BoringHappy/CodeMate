# CodeMate

English | [简体中文](README_CN.md)

Docker-based Claude Code and Codex environment with automated Git/PR setup.

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
- tmux session management with native Claude/Codex Stop-hook PR monitoring

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

#### Basic Commands

```bash
# First time setup - creates global config and project .env
codemate --setup

# Run with explicit repo URL
codemate --repo https://github.com/your-org/your-repo.git --branch feature/xyz

# Run with branch name (auto-detects repo from: --repo > .env > current directory's git remote)
codemate --branch feature/your-branch

# Run Codex instead of the default Claude runtime
codemate --branch feature/your-branch --agent codex

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

# Run with custom volume mounts (optional)
codemate --branch feature/xyz --mount ~/data:/data

# Run with initial query to Claude
codemate --branch feature/xyz --query "Please review the code and fix any issues"

# Build and run from local Dockerfile
codemate --build --branch feature/xyz

# Build with custom Dockerfile path and tag
codemate --build -f ./custom/Dockerfile --tag my-codemate:v1 --branch feature/xyz

# For Chinese users: Use DaoCloud mirror for faster image pulls
codemate --branch feature/xyz --image ghcr.m.daocloud.io/boringhappy/codemate:latest

# Pass arbitrary Docker run parameters (e.g. enable GPU access)
codemate --branch feature/xyz --docker-param "--gpus all"

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

##### Custom Volume Mounts

Use `--mount <host-path>:<container-path>` to mount additional directories or files. Useful for sharing data, configurations, or credentials with the container. Multiple `--mount` options can be specified.

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
- `-f, --dockerfile PATH` - Path to Dockerfile (default: `docker/Dockerfile`)
- `--tag TAG` - Image tag for local build (default: `codemate:local`)
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
| `CODEMATE_HOME` | No | CodeMate home directory on the host; supports `~` and `$VAR` expansion (default: `~/.codemate`) |
| `CODEMATE_AGENT` | No | Runtime to launch: `claude` (default) or `codex` |
| `CODEMATE_AGENT_SESSION` | No | Override the tmux session name (defaults to an instance-scoped name) |
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

`CODEMATE_BRANCH_NAME`, `CODEMATE_PR_NUMBER`, `CODEMATE_PR_TITLE`, `CODEMATE_ISSUE_NUMBER`, `CODEMATE_QUERY`, `CODEMATE_NO_PR`, `CODEMATE_CHAT`, and `CODEMATE_CO_AUTHOR_BY` can be set through CLI options, `.env`, or ambient environment variables. Prefer CLI options for one-off runs. Use `codemate --agent claude|codex` to override `CODEMATE_AGENT` from `.env` for a single run, `codemate --chat` to skip PR creation and CodeMate system prompt injection, and `codemate --co-author-by "Name <email@example.com>"` to add a co-author for commits made by the Git commit skill.


## How It Works

CodeMate uses a separate [base image (`codemate-base`)](https://github.com/BoringHappy/CodeMate/pkgs/container/codemate-base) that is rebuilt weekly to keep system packages and development tools up-to-date.

On startup, the container:
1. Configures git user from environment variables
2. Authenticates GitHub CLI with token
3. Clones/updates repository to `/home/agent/<repo-name>`
4. Checks out the specified branch or PR
5. Creates a draft PR if working on a new branch (unless `--no-pr`, `--chat`, or fork workflow)
6. Installs/updates plugins for the selected agent from configured marketplaces
7. Starts Claude Code or Codex in the matching tmux session, appending CodeMate instructions unless chat mode is enabled
8. Sends the initial query to the selected agent if `--query` is provided
9. Uses the workspace plugin's Stop hook to monitor PR comments, CI failures, and review-ready state while the agent is idle

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

CodeMate monitors PR feedback from the workspace plugin's native `Stop` hook. The first check runs immediately; later checks back off to 10, 30, 60, and then at most 120 seconds. No cron daemon or tmux prompt injection is used. Claude runs the poller as an `asyncRewake` hook so the UI remains interactive; Codex uses its synchronous Stop continuation contract because Codex does not currently run async command hooks.

The hook verifies that its own session is still stopped and that the current worktree/branch still has an open PR before every `gh` call. It also watches the agent's prompt history (`$CODEX_HOME/history.jsonl` for Codex, `$CLAUDE_CONFIG_DIR/history.jsonl` for Claude): a new user prompt is recorded there the moment it is submitted, so even while Codex is still blocked running the Stop hook, the in-flight monitor notices within a second and exits, letting the new message resume the session without pressing Esc. When feedback is found, Claude is awakened through `asyncRewake`; Codex receives a structured Stop continuation decision. Both paths create a native agent turn.

### State Isolation

- Session status is keyed by runtime instance, agent, and `session_id`; notification commit baselines and retry counters are partitioned again by Git worktree and branch.
- PR lifecycle state is stored at `<absolute-git-dir>/codemate/pr-status/<branch>.json`; adjacent monitor-state and lock files hold shared cursors and an interruptible branch lease, so only one stopped session handles a given PR event.
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
