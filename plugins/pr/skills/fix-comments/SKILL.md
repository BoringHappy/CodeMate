---
name: fix-comments
description: Reads comments from a GitHub pull request, fixes the issues mentioned in the comments, commits the changes, and replies to the comments. Use when the user wants to address PR feedback or fix issues mentioned in code reviews.
---

# Fix PR Comments

Automatically address feedback from GitHub pull request comments.

## What it does

1. **Reads PR comments**: Uses `/pr:get-details` skill to fetch and display all comments (both PR-level and code review comments) from the current pull request
2. **Filters addressed comments**: Skips comment threads where the last reply starts with "CodeMate Replied:" (these have already been addressed)
3. **Parses feedback**: Analyzes each unresolved comment to understand what needs to be fixed
4. **Reads affected files**: Uses the Read tool to examine files mentioned in comments
5. **Applies fixes**: Makes the necessary code changes using the Edit or Write tools
6. **Commits and pushes changes**: Uses the `/git:commit` skill to stage, commit with a descriptive message, and push changes to the remote branch
7. **Replies to comments**: Uses `gh api -X POST repos/:owner/:repo/pulls/{pr}/comments/{comment_id}/replies` to reply directly to each review comment thread, confirming the fix. **IMPORTANT**: All replies must start with "CodeMate Replied:" to mark the thread as resolved and prevent re-triggering

## Prerequisites

**Check PR Status:**
!`if [ -s /tmp/.pr_status ]; then echo "[OK] PR exists: $(cat /tmp/.pr_status)"; else echo "[WARN] No PR created yet"; fi`

**Before proceeding, verify PR exists:**
```bash
if [ ! -s /tmp/.pr_status ]; then
    echo "[ERROR] No PR has been created yet."
    exit 1
fi
```

- Must be run in a git repository
- GitHub CLI (`gh`) must be installed and authenticated
- Must have write access to the repository
- Pull request must exist for the current branch
- Requires `/pr:get-details` skill to be available
- Requires `/git:commit` skill to be available

## Technical Details

- Uses `/pr:get-details` skill to fetch both PR-level and code review comments in a formatted way
- The `/pr:get-details` skill internally uses `gh pr view` and `gh api` to gather all comment information
- **Identifying addressed comments**: A comment thread is considered addressed if its last reply starts with "CodeMate Replied:"
- Only processes unresolved comments (those without "CodeMate Replied:" in the last reply)
- Uses `/git:commit` skill to stage, commit, and push changes to the remote branch
- Replies use `gh api -X POST repos/:owner/:repo/pulls/{pr}/comments/{comment_id}/replies` to thread responses
- **Reply Format**: All replies must start with "CodeMate Replied:" to mark threads as resolved
- Handles multiple comments in a single run

## Notes

- The command will process all unresolved review comments on the PR
- **Identifying resolved comments**: If a comment thread's last reply starts with "CodeMate Replied:", it means the comment has been addressed and will be skipped
- Each fix is committed separately for better tracking
- Replies are added to the specific comment thread, not as new top-level comments
- **Reply Format**: All comment replies must start with "CodeMate Replied:" to prevent the monitoring system from re-triggering on already-handled feedback
- The monitoring system automatically filters out threads with "CodeMate Replied:" in the last reply
