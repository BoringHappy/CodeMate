---
name: create
description: Creates a pull request from current branch to target repository. Supports both standard and fork workflows. Use when ready to submit changes for review.
context: fork
---

# Create Pull Request

Creates a pull request with an appropriate title and description based on your commits.

## Current State

Current branch:
!`git branch --show-current`

Upstream remote (for fork workflow):
!`git remote get-url upstream 2>/dev/null || echo "No upstream configured (standard workflow)"`

Origin remote:
!`git remote get-url origin`

Recent commits to include in PR:
!`git log origin/$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "main")..HEAD --oneline 2>/dev/null || git log --oneline -5`

Diff summary:
!`git diff --stat HEAD~1..HEAD 2>/dev/null || echo "No commits yet"`

## PR Template

!`if [ -f .github/PULL_REQUEST_TEMPLATE.md ]; then cat .github/PULL_REQUEST_TEMPLATE.md; elif [ -f .github/pull_request_template.md ]; then cat .github/pull_request_template.md; elif [ -f pull_request_template.md ]; then cat pull_request_template.md; else echo "No template found - will use default format"; fi`

## Instructions

### 1. Verify Changes Are Pushed

Ensure your branch is pushed to origin:
```bash
CURRENT_BRANCH=$(git branch --show-current)
git push -u origin "$CURRENT_BRANCH"
```

### 2. Generate PR Title and Description

Based on the commits and changes above:
- **Title**: Create a concise title (50-72 characters) that clearly describes the main change
  - Use imperative mood (e.g., "Add feature" not "Added feature")
  - Be specific and descriptive
- **Description**: Write a clear description that:
  - Follows the PR template format if one exists
  - Explains what changes were made and why
  - Highlights key technical details
  - Includes any relevant context

### 3. Detect Workflow Type and Create PR

**Check if this is a fork workflow:**
```bash
if git remote get-url upstream &>/dev/null; then
    echo "Fork workflow detected"
    WORKFLOW="fork"
else
    echo "Standard workflow detected"
    WORKFLOW="standard"
fi
```

**For Fork Workflow:**
```bash
# Extract repository information
UPSTREAM_REPO=$(git remote get-url upstream | sed 's/.*github.com[:/]//' | sed 's/.git$//')
ORIGIN_URL=$(git remote get-url origin)
FORK_OWNER=$(echo "$ORIGIN_URL" | sed 's/.*github.com[:/]//' | sed 's/.git$//' | cut -d'/' -f1)
CURRENT_BRANCH=$(git branch --show-current)

# Get default branch of upstream
DEFAULT_BRANCH=$(gh api repos/$UPSTREAM_REPO --jq .default_branch)

# Create cross-repo PR
gh pr create \
  --repo "$UPSTREAM_REPO" \
  --head "$FORK_OWNER:$CURRENT_BRANCH" \
  --base "$DEFAULT_BRANCH" \
  --title "Your generated title here" \
  --body "Your generated description here"

# Capture PR URL
PR_URL=$(gh pr view --repo "$UPSTREAM_REPO" --json url -q .url)
```

**For Standard Workflow:**
```bash
# Create PR in same repository
gh pr create \
  --title "Your generated title here" \
  --body "Your generated description here"

# Capture PR URL
PR_URL=$(gh pr view --json url -q .url)
```

### 4. Persist the PR Reference

**IMPORTANT**: After successfully creating the PR, record it through the pr
plugin's own `pr-status` interface. The cache is keyed by Git worktree + branch
under the runtime root, so concurrent repositories, branches, and agent
sessions cannot overwrite each other. If the interface script cannot be
located (e.g. bare manual installs), fall back to the legacy per-worktree
status file so older workspace hooks keep working:
```bash
CURRENT_BRANCH=$(git branch --show-current)
[ -n "$CURRENT_BRANCH" ] || CURRENT_BRANCH="detached-$(git rev-parse --short=12 HEAD)"
PR_NUMBER=${PR_URL%/}
PR_NUMBER=${PR_NUMBER##*/}

PR_STATUS_SCRIPT=""
for _cand in \
    "${CODEMATE_PR_PLUGIN_ROOT:-}/scripts/pr-status.sh" \
    "${CODEMATE_PLUGIN_ROOT:-}/../pr/scripts/pr-status.sh" \
    "${PLUGIN_ROOT:-}/../pr/scripts/pr-status.sh" \
    "${CLAUDE_PLUGIN_ROOT:-}/../pr/scripts/pr-status.sh"; do
    [ -n "$_cand" ] && [ -x "$_cand" ] && { PR_STATUS_SCRIPT="$_cand"; break; }
done

if [ -n "$PR_STATUS_SCRIPT" ]; then
    "$PR_STATUS_SCRIPT" set --number "$PR_NUMBER" --url "$PR_URL" --branch "$CURRENT_BRANCH"
else
    PR_STATUS_FILE="$(git rev-parse --absolute-git-dir)/codemate/pr-status/$CURRENT_BRANCH.json"
    mkdir -p "$(dirname "$PR_STATUS_FILE")"
    PR_STATUS_TMP=$(mktemp "$(dirname "$PR_STATUS_FILE")/.pr-status.XXXXXX")
    jq -n \
      --arg branch "$CURRENT_BRANCH" \
      --arg url "$PR_URL" \
      --argjson number "$PR_NUMBER" \
      --arg updated_at "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
      '{state: "open", branch: $branch, number: $number, url: $url, updated_at: $updated_at}' \
      > "$PR_STATUS_TMP"
    mv "$PR_STATUS_TMP" "$PR_STATUS_FILE"
fi
echo "✓ PR created and status saved: $PR_URL"
```

### 5. Display Success Message

Show the user:
- PR URL
- PR number
- Next steps (e.g., "PR created successfully! Reviewers will be notified.")

## Prerequisites

- Must have commits to include in PR
- Branch must be pushed to origin
- For fork workflow: upstream remote must be configured
- GitHub CLI (`gh`) must be authenticated

## Notes

- This skill handles both standard and fork workflows automatically
- For fork workflows, it creates a cross-repo PR from your fork to the upstream repository
- PR state lives in GitHub; the local cache is keyed by Git worktree + branch
  under the runtime root (legacy per-worktree files are only written as a
  fallback for older workspace hooks)
