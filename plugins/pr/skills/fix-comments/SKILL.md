---
name: fix-comments
description: Reads comments from a GitHub pull request, fixes the issues mentioned in the comments, commits the changes, and replies to the comments. Use when the user wants to address PR feedback or fix issues mentioned in code reviews.
---

# Fix PR Comments

Automatically address feedback from GitHub pull request comments.

## Shared Contract

Reply-prefix and acknowledgment conventions are shared with the workspace
monitor (see `docs/plugin-contracts.md`). The default reply prefix is
`CodeMate Replied:`; deployments may override it with the
`CODEMATE_REPLY_PREFIX` environment variable. Always use the effective prefix
so the monitor recognizes resolved threads.

## What it does

1. **Reads PR comments**: Uses comment details supplied in the user prompt when present. Only use `/pr:get-details` if the prompt does not include enough comment context.
2. **Filters addressed comments**: Skips comment threads where the last reply starts with "CodeMate Replied:" (these have already been addressed)
3. **Parses feedback**: Analyzes each unresolved comment to understand what needs to be fixed
4. **Reads affected files**: Uses the Read tool to examine files mentioned in comments
5. **Applies fixes**: Makes the necessary code changes using the Edit or Write tools
6. **Commits and pushes changes**: Uses the `/git:commit` skill to stage, commit with a descriptive message, and push changes to the remote branch
7. **Replies to comments**: Uses `gh api -X POST repos/:owner/:repo/pulls/{pr}/comments/{comment_id}/replies` to reply directly to each review comment thread, confirming the fix. **IMPORTANT**: All replies must start with "CodeMate Replied:" to mark the thread as resolved and prevent re-triggering

## Prerequisites

**Check PR Status:**
!`CURRENT_BRANCH=$(git branch --show-current); [ -n "$CURRENT_BRANCH" ] || CURRENT_BRANCH="detached-$(git rev-parse --short=12 HEAD)"; if git remote get-url upstream >/dev/null 2>&1; then UPSTREAM_REPO=$(git remote get-url upstream | sed 's/.*github.com[:/]//' | sed 's/.git$//'); FORK_OWNER=$(git remote get-url origin | sed 's/.*github.com[:/]//' | sed 's/.git$//' | cut -d'/' -f1); PR=$(gh pr list --repo "$UPSTREAM_REPO" --head "$FORK_OWNER:$CURRENT_BRANCH" --state open --json number,url,state -q '.[0]'); else PR=$(gh pr list --head "$CURRENT_BRANCH" --state open --json number,url,state -q '.[0]'); fi; if [ -z "$PR" ] || [ "$PR" = "null" ]; then echo "[ERROR] No open pull request found for branch '$CURRENT_BRANCH'. Create one with /pr:create first."; exit 1; fi; echo "[OK] PR #$(printf '%s' "$PR" | jq -r .number): $(printf '%s' "$PR" | jq -r .url)"`

**Before proceeding, verify PR exists:**
```bash
CURRENT_BRANCH=$(git branch --show-current)
[ -n "$CURRENT_BRANCH" ] || CURRENT_BRANCH="detached-$(git rev-parse --short=12 HEAD)"
if git remote get-url upstream >/dev/null 2>&1; then
  UPSTREAM_REPO=$(git remote get-url upstream | sed 's/.*github.com[:/]//' | sed 's/.git$//')
  FORK_OWNER=$(git remote get-url origin | sed 's/.*github.com[:/]//' | sed 's/.git$//' | cut -d'/' -f1)
  PR=$(gh pr list --repo "$UPSTREAM_REPO" --head "$FORK_OWNER:$CURRENT_BRANCH" --state open --json number,url,state -q '.[0]')
else
  PR=$(gh pr list --head "$CURRENT_BRANCH" --state open --json number,url,state -q '.[0]')
fi
if [ -z "$PR" ] || [ "$PR" = "null" ]; then
    echo "[ERROR] No open pull request found for branch '$CURRENT_BRANCH'. Create one with /pr:create first."
    exit 1
fi
echo "[OK] PR #$(printf '%s' "$PR" | jq -r .number): $(printf '%s' "$PR" | jq -r .url)"
```

- Must be run in a git repository
- GitHub CLI (`gh`) must be installed and authenticated
- Must have write access to the repository
- Pull request must exist for the current branch
- Requires `/git:commit` skill to be available

## Technical Details

- When the prompt includes `comment_id` values, use those IDs directly for replies and do not fetch PR comments again unless required to understand the requested fix
- If comment context is missing, use `/pr:get-details` to fetch both PR-level and code review comments in a formatted way
- **Identifying addressed comments**: A comment thread is considered addressed if its last reply starts with "CodeMate Replied:"
- Only processes unresolved comments (those without "CodeMate Replied:" in the last reply)
- Uses `/git:commit` skill to stage, commit, and push changes to the remote branch
- Replies use `gh api -X POST repos/:owner/:repo/pulls/{pr}/comments/{comment_id}/replies` to thread responses, where `{pr}` and `{comment_id}` come from the prompt when provided
- **Reply Format**: All replies must start with "CodeMate Replied:" to mark threads as resolved
- Handles multiple comments in a single run

## Notes

- The command will process all unresolved review comments on the PR
- **Identifying resolved comments**: If a comment thread's last reply starts with "CodeMate Replied:", it means the comment has been addressed and will be skipped
- Each fix is committed separately for better tracking
- Replies are added to the specific comment thread, not as new top-level comments
- **Reply Format**: All comment replies must start with "CodeMate Replied:" to prevent the monitoring system from re-triggering on already-handled feedback
- The monitoring system automatically filters out threads with "CodeMate Replied:" in the last reply
