---
name: update
description: Updates the summary/description and optionally the title of a GitHub pull request. Use `/pr:update` to update both title and summary, or `/pr:update --summary-only` to update only the summary.
context: fork
---

# Update PR Summary and Title

Generates an improved PR description and optionally title, then updates the PR via GitHub API.

## Arguments

$ARGUMENTS

**Supported arguments:**
- `--summary-only`: Only update the PR summary/description, skip title update
- (no arguments): Update both title and summary (default behavior)

## Prerequisites

!```bash
if [ ! -s /tmp/.pr_status ]; then
    echo "[ERROR] No PR has been created yet."
    exit 1
fi
echo "[OK] PR exists: $(cat /tmp/.pr_status)"
```

## PR Template

!```bash
for f in .github/PULL_REQUEST_TEMPLATE.md .github/pull_request_template.md pull_request_template.md; do
    [ -f "$f" ] && cat "$f" && break
done
```

## Instructions

Use the PR details already in context (from `/pr:get-details` called at conversation start) to generate an updated description and optionally title.

### Summary
- Accurately describe what changed and why
- Follow the template format above if one was found
- Highlight key changes and their impact
- **Never modify checkbox item text** — only toggle `[x]`/`[ ]` state

### Title (skip if `--summary-only`)
- Concise (50-72 characters), imperative mood
- Follows conventional commit style if the project uses it

### Update the PR

**Summary only (`--summary-only`):**
```bash
gh api repos/:owner/:repo/pulls/$(gh pr view --json number -q .number) -X PATCH -f body='BODY'
```

**Both title and summary (default):**
```bash
gh api repos/:owner/:repo/pulls/$(gh pr view --json number -q .number) -X PATCH -f title="TITLE" -f body='BODY'
```

### Add `pr-updated` label
```bash
gh api repos/:owner/:repo/issues/$(gh pr view --json number -q .number)/labels --input - <<< '["pr-updated"]'
```
