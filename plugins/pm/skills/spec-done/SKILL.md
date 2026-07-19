---
name: spec-done
description: Summarizes sub-issue and PR changes, writes a done summary comment, closes the spec issue, and adds the done label. Use when a spec is fully implemented and ready to be marked complete.
argument-hint: <issue-number>
---

# Spec Done

Marks a spec as complete by summarizing all sub-issue and PR changes, posting a done summary, closing the issue, and adding the `done` label.

Usage: `/pm:spec-done <issue-number>`

## Preflight

!```bash
if [ -z "$ARGUMENTS" ]; then
  echo "[ERROR] No issue number provided. Usage: /pm:spec-done <issue-number>"
  exit 1
fi
```

!```bash
echo "--- Fetching spec issue ---"
spec=$(gh issue view "$ARGUMENTS" --json number,title,url,body,state,labels 2>/dev/null)
if [ -z "$spec" ] || [ "$spec" = "null" ]; then echo "[ERROR] Issue #$ARGUMENTS not found"; exit 1; fi
echo "$spec" | jq -r '"[OK] #\(.number) [\(.state)]: \(.title)\nURL: \(.url)"'
echo ""
echo "--- Sub-issues ---"
REPO=$(gh repo view --json nameWithOwner -q '.nameWithOwner')
gh api "/repos/$REPO/issues/$ARGUMENTS/sub_issues" \
  --jq '.[] | "#\(.number) [\(.state | ascii_upcase)] \(.title)"' 2>/dev/null || echo "(none)"
echo ""
echo "--- Linked PRs (from sub-issue timelines) ---"
SUB_NUMS=$(gh api "/repos/$REPO/issues/$ARGUMENTS/sub_issues" --jq '.[].number' 2>/dev/null)
for n in $SUB_NUMS; do
  gh api "/repos/$REPO/issues/$n/timeline" \
    --jq ".[] | select(.event == \"cross-referenced\") | select(.source.issue.pull_request != null) | \"  PR #\(.source.issue.number): \(.source.issue.title) [\(.source.issue.state | ascii_upcase)]\"" 2>/dev/null
done
```

## Instructions

Using the spec issue, sub-issues, and linked PRs fetched above:

1. **Build a done summary** covering:
   - What was built (one sentence per sub-issue/PR, grouped by area if helpful)
   - Total tasks completed and PRs merged

2. **Ensure `done` label exists and post the summary comment, then close the issue:**

   ```bash
   REPO=$(gh repo view --json nameWithOwner -q '.nameWithOwner')

   gh label create "done" --color "0E8A16" --description "Spec is complete" --force 2>/dev/null || true

   DONE_SUMMARY_FILE=$(mktemp "${TMPDIR:-/tmp}/codemate-spec-done.XXXXXX")
   trap 'rm -f "$DONE_SUMMARY_FILE"' EXIT
   printf '%s' "<done summary markdown>" > "$DONE_SUMMARY_FILE"
   gh issue comment "$ARGUMENTS" --body-file "$DONE_SUMMARY_FILE"
   rm -f "$DONE_SUMMARY_FILE"
   trap - EXIT

   gh issue edit "$ARGUMENTS" --add-label "done"
   gh issue close "$ARGUMENTS"
   ```

3. **Output confirmation:**
   ```
   ✅ Spec #<number> marked as done and closed.
   ```

## Prerequisites
- A spec issue must exist
- GitHub CLI authenticated
