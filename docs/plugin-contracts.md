# Plugin Contracts

This document is the single source of truth for conventions shared between the
CodeMate plugins. Both the `pr` plugin and the `workspace` plugin reference
these contracts; they must stay in sync here before either side changes a
value. Every contract value is overridable via environment, so deployments can
rebrand or reuse the plugins without editing code.

## PR State Resolution

**GitHub is the source of truth for pull request state.** There is no shared
local PR-status file between plugins.

- The current PR for a worktree is the open PR whose head ref matches the
  current branch:
  - Standard workflow: `gh pr list --head <branch> --state open --json number,url,state -q '.[0]'`
  - Fork workflow: `gh pr list --repo <upstream> --head <fork-owner>:<branch> --state open --json number,url,state -q '.[0]'`
- The canonical implementation lives in the pr plugin:
  `plugins/pr/scripts/pr-status.sh` (`get` / `set` / `clear`). It is
  query-first, cache-backed, and fork-aware.
- The workspace hooks consume the pr plugin through `pr-status.sh get`. If the
  script cannot be located, the hooks fall back to the inline query above
  (same contract). The workspace plugin never parses a shared PR-state file.
- The pr plugin's local cache is private to the pr plugin, lives under the
  runtime root (never inside `.git`), and is keyed by Git worktree + branch.
  It is used only for disambiguation and as an offline / eventual-consistency
  fallback.
- Legacy per-worktree files at
  `<git-dir>/codemate/pr-status/<branch>.json` (written by older pr versions)
  are migrated to the plugin-owned cache on first read and are otherwise
  deprecated.

## Reply Prefix (resolved threads)

- Value: `CodeMate Replied:`
- Env override: `CODEMATE_REPLY_PREFIX`
- The `pr:fix-comments` skill starts every reply to a review thread with this
  prefix. The workspace monitor treats a thread whose last reply starts with
  the prefix as resolved and skips it.

## Acknowledgment Reaction

- Value: `eyes` (renders as 👀)
- Env override: `CODEMATE_ACK_REACTION`
- The `pr:ack-comments` skill adds this reaction to issue comments. The
  workspace monitor skips comments that already carry the configured reaction.

## Ready-for-Review Label

- Value: `pr-updated`
- Env override: `CODEMATE_PR_UPDATED_LABEL`
- The `pr:update` skill applies this label after updating the PR. The
  workspace monitor notifies once and then skips PRs carrying the label.

## Skill References in Monitor Messages

The monitor emits generic instructions plus structured payloads
(`comment_id`, `path`, `line`, `body`) rather than hardcoding skill names.
The default skill names it may reference:

| Purpose | Default command | Env override |
|---|---|---|
| Commit + push | `git:commit` | `CODEMATE_COMMIT_COMMAND` |
| Fix review comments | `pr:fix-comments` | `CODEMATE_FIX_COMMENTS_COMMAND` |
| Acknowledge issue comments | `pr:ack-comments` | `CODEMATE_ACK_COMMENTS_COMMAND` |
| Update PR summary/title | `pr:update` | `CODEMATE_UPDATE_COMMAND` |

The mapping from a continuation message to the actual skill is made by the
agent (Claude Code or Codex), so plugins stay decoupled at the behavior layer.

## Runtime State Location

- Session status and notification baselines: under the runtime root, scoped by
  runtime instance + agent + session ID.
- Monitor cursors, locks, and PR-state cache: under the runtime root, keyed by
  Git worktree + branch. Never inside `.git`.
- Runtime root resolution order:
  `CODEMATE_RUNTIME_DIR` → `CODEMATE_TMPDIR/codemate` →
  `$XDG_RUNTIME_DIR/codemate` → `${TMPDIR:-/tmp}/codemate-$(id -u)`.

## Plugin Dependency Direction

- `git` — base: commit/push.
- `pr` — owns PR semantics; depends on `git` (via `pr:fix-comments`).
- `workspace` — owns session lifecycle and PR-feedback monitoring; consumes the
  `pr` plugin's contract. The `pr` plugin never depends on `workspace`.
