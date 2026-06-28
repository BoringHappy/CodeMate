---
name: update
description: Updates the summary/description and (by default) the title of a GitHub pull request. Use `/pr:update` to update both title and summary, or `/pr:update --skip-title` to update only the summary.
context: fork
---

# Update PR Summary and Title

Generates an improved PR description (and optionally title) from the branch's commits and diff, then updates the PR.

> This skill runs in a **forked context**, so it gathers its own state below. It does **not** inherit `/pr:get-details` output from the main conversation, and it deliberately avoids `/pr:get-details` (which fetches reviews/comments and never returns the code diff — irrelevant and costly here).

## Arguments

$ARGUMENTS

- `--skip-title`: skip the title update, change only the description
- (no arguments): update both title and description

## Prerequisites

!```bash
if [ ! -s /tmp/.pr_status ]; then
    echo "[ERROR] No PR has been created yet."
    exit 1
fi
echo "[OK] PR: $(cat /tmp/.pr_status)"
```

## PR context and change overview

Fetched from GitHub in a single `gh pr view` call — correct for both standard and fork PRs (no dependence on local base-branch refs):

!```bash
META=$(gh pr view --json number,baseRefName,title,body,commits,files)

echo "PR_NUMBER: $(printf '%s' "$META" | jq -r .number)"
echo "BASE_BRANCH: $(printf '%s' "$META" | jq -r .baseRefName)"
echo
echo "=== Current title ==="
printf '%s' "$META" | jq -r .title
echo
echo "=== Current description (preserve checkbox text; only toggle [ ]/[x]) ==="
printf '%s' "$META" | jq -r .body
echo
echo "=== Commits (primary source for the summary) ==="
printf '%s' "$META" | jq -r '.commits[] | "\(.oid[0:9]) \(.messageHeadline)", (.messageBody | select(. != ""))'
echo
echo "=== Files changed ==="
printf '%s' "$META" | jq -r '.files[] | "+\(.additions) -\(.deletions)\t\(.path)"'
```

## PR template

!```bash
for f in .github/PULL_REQUEST_TEMPLATE.md .github/pull_request_template.md pull_request_template.md; do
    [ -f "$f" ] && cat "$f" && break
done
```

## Instructions

### 1. Decide whether you need the full diff
The commit log and file-level diffstat above are your primary source. Write the summary directly from them **when the commit messages clearly describe the changes**. Only when they are vague or insufficient, read the actual code changes:

```bash
gh pr diff   # full unified diff for this PR (fork-correct); the main token cost
```

Skip this whenever the commits already explain the changes; for a targeted look, focus on the files listed in the diffstat above.

### 2. Write the summary
- Accurately describe what changed and why, based on the commits/diff above
- Follow the template format if one was found; otherwise keep it clear and structured
- **Preserve existing checkbox text verbatim** — only change `[ ]` ↔ `[x]`

### 3. Write the title (skip if `--skip-title`)
- Concise (≈50–72 chars), imperative mood, conventional-commit style if the repo uses it

### 4. Update the PR
**Skip title (`--skip-title`):**
```bash
gh api repos/:owner/:repo/pulls/$(gh pr view --json number -q .number) -X PATCH -f body='BODY'
```

**Both title and summary (default):**
```bash
gh api repos/:owner/:repo/pulls/$(gh pr view --json number -q .number) -X PATCH -f title="TITLE" -f body='BODY'
```

### 5. Add the `pr-updated` label
```bash
gh api repos/:owner/:repo/issues/$(gh pr view --json number -q .number)/labels --input - <<< '["pr-updated"]'
```
