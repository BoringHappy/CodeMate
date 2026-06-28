---
name: get-details
description: Gets details of a GitHub pull request including title, description, file changes, and review comments. Use when the user wants to view PR information.
---

# Get PR Details

Retrieves and displays pull request information including title, description, changed files, and review comments.

## Prerequisites

!```bash
if [ ! -s /tmp/.pr_status ]; then
    echo "[ERROR] No PR has been created yet."
    exit 1
fi
echo "[OK] PR exists: $(cat /tmp/.pr_status)"
```

## PR Information

Fetched in a single `gh pr view` call (plus one `gh api` call for inline code comments) to keep API usage and tokens low:

!```bash
PR=$(gh pr view --json number,title,headRefName,baseRefName,body,files,reviews,comments)
N=$(printf '%s' "$PR" | jq -r '.number')

echo "Title:"
printf '%s' "$PR" | jq -r '.title'

echo; echo "Branch:"
printf '%s' "$PR" | jq -r '"\(.headRefName) → \(.baseRefName)"'

echo; echo "Description:"
printf '%s' "$PR" | jq -r '.body'

echo; echo "Changed files:"
printf '%s' "$PR" | jq -r '.files[].path'

echo; echo "Review comments:"
printf '%s' "$PR" | jq -r '.reviews[] | "**\(.author.login)** (\(.state)) - \(.submittedAt):\n\(.body)\n"'

echo; echo "Inline review comments (code comments):"
gh api repos/:owner/:repo/pulls/"$N"/comments --jq '.[] | "**\(.user.login)** on \(.path):\(.line) - \(.created_at) [comment_id:\(.id)]:\n\(.body)\n"'

echo; echo "PR comments:"
printf '%s' "$PR" | jq -r '.comments[] | "**\(.author.login)** - \(.createdAt):\n\(.body)\n"'
```

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
