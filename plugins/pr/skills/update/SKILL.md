---
name: update
description: Updates the summary/description and optionally the title of a GitHub pull request. Use `/pr:update` to update both title and summary, or `/pr:update --summary-only` to update only the summary.
context: fork
---

# Update PR Summary and Title

Analyzes the current pull request changes and generates an improved summary/description and optionally title.

## Arguments

$ARGUMENTS

**Supported arguments:**
- `--summary-only`: Only update the PR summary/description, skip title update
- (no arguments): Update both title and summary (default behavior)

## What it does

1. **Fetches PR information**: Uses `/pr:get-details` skill to get the current PR title, description, diff, and metadata
2. **Checks for template**: Looks for pull_request_template.md to follow the project's format
3. **Generates improved summary**: Creates a better description based on the actual changes
4. **Generates improved title**: Creates a concise, descriptive title based on the changes (unless `--summary-only` is specified)
5. **Updates the PR**: Uses `gh api` REST API to update the PR

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

## Current PR Information

**IMPORTANT**: Before proceeding, you MUST use the `/pr:get-details` skill to fetch the current PR information including:
- Current PR title
- Current PR description/body
- PR diff showing all changes
- List of files changed

This provides the necessary context to generate an improved summary and title.

## Template Format

!`if [ -f .github/PULL_REQUEST_TEMPLATE.md ]; then cat .github/PULL_REQUEST_TEMPLATE.md; elif [ -f .github/pull_request_template.md ]; then cat .github/pull_request_template.md; elif [ -f pull_request_template.md ]; then cat pull_request_template.md; else echo "No template found"; fi`

## Instructions

Based on the PR diff and current information above:

### For the Summary
Create an improved PR summary that:
- Accurately describes what changes were made and why
- Follows the template format if one exists
- Is clear, concise, and informative
- Highlights the key changes and their impact
- Includes relevant technical details
- **IMPORTANT**: Never modify the text content of checkbox items (e.g., `- [ ] Tests pass locally`). Only change whether checkboxes are checked `[x]` or unchecked `[ ]`. The checkbox text must remain exactly as it appears in the template or existing PR description.

### For the Title (skip if `--summary-only` argument is provided)
Create an improved PR title that:
- Is concise (typically 50-72 characters)
- Clearly describes the main change or feature
- Follows conventional commit style if the project uses it
- Is written in imperative mood (e.g., "Add feature" not "Added feature")
- Captures the essence of all changes

### Updating the PR

**If `--summary-only` argument is provided**, only update the summary:
```bash
gh api repos/:owner/:repo/pulls/$(gh pr view --json number -q .number) -X PATCH -f body='Your improved summary here'
```

**If no arguments or updating both**, update both title and summary using a single API call:
```bash
gh api repos/:owner/:repo/pulls/$(gh pr view --json number -q .number) -X PATCH -f title="Your improved title here" -f body='Your improved summary here'
```

### Adding the `pr-updated` label

After successfully updating the PR, add the `pr-updated` label so monitors and reviewers can see the PR description has been refreshed:

```bash
gh api repos/:owner/:repo/issues/$(gh pr view --json number -q .number)/labels --input - <<< '["pr-updated"]'
```

This step runs in both `--summary-only` and default modes.

## Prerequisites

- Must be run in a git repository with an active pull request
- GitHub CLI (`gh`) must be installed and authenticated
- Must have write access to the repository
