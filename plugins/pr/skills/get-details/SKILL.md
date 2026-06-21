---
name: get-details
description: Gets details of a GitHub pull request including title, description, file changes, and review comments. Use when the user wants to view PR information.
---

# Get PR Details

Retrieves and displays pull request information including title, description, changed files, and review comments.

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

## PR Information

Title:
!`gh pr view --json title -q .title | cat`

Branch:
!`gh pr view --json headRefName,baseRefName -q '"\(.headRefName) → \(.baseRefName)"' | cat`

Description:
!`gh pr view --json body -q .body | cat`

Changed files:
!`gh pr view --json files -q '.files[].path' | cat`

Review comments:
!`gh pr view --json reviews -q '.reviews[] | "**\(.author.login)** (\(.state)) - \(.submittedAt):\n\(.body)\n"' | cat`

Inline review comments (code comments):
!`gh api repos/:owner/:repo/pulls/$(gh pr view --json number -q .number)/comments --jq '.[] | "**\(.user.login)** on \(.path):\(.line) - \(.created_at) [comment_id:\(.id)]:\n\(.body)\n"' | cat`

PR comments:
!`gh pr view --json comments -q '.comments[] | "**\(.author.login)** - \(.createdAt):\n\(.body)\n"' | cat`

## Instructions

**IMPORTANT: You MUST output a summary to the user.** After gathering the PR information above, display a formatted summary that includes:

1. **PR Title** - The pull request title
2. **Branch** - Source branch → target branch
3. **Description** - The PR description/body (summarized if lengthy)
4. **Changed Files** - List of files modified in this PR
5. **Review Comments** - Summary of overall review feedback (if any)
6. **Inline Review Comments** - Code-specific comments attached to lines (if any)
7. **PR Comments** - Summary of general comments (if any)

Format the output clearly using markdown so the user can see the PR details at a glance. This summary should always be visible in your response to the user.

> Always pass `--json` with explicit fields to `gh pr view`/`gh pr list`; bare calls fail on the Projects (classic) deprecation.
