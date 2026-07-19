---
name: spec-decompose
description: Fetches the spec GitHub Issue, parses the task breakdown table, creates task issues as sub-issues, and links them to the spec. Reconciles existing sub-issues on re-decompose. Use after spec-plan to prepare tasks. Accepts an optional --granularity flag (micro | pr | macro) to control task splitting.
argument-hint: <issue-number> [--granularity micro|pr|macro]
---

# Spec Decompose

Reads the Task Breakdown table from the spec GitHub Issue, creates individual task issues as sub-issues, and reconciles any changes on re-runs.

Usage: `/pm:spec-decompose <issue-number> [--granularity micro|pr|macro]`

## Preflight

!```bash
if [ -z "$ARGUMENTS" ]; then
  echo "[ERROR] No issue number provided. Usage: /pm:spec-decompose <issue-number> [--granularity micro|pr|macro]"
  exit 1
fi
```

!```bash
ARG=$(echo "$ARGUMENTS" | awk '{print $1}')
GRAN=""
if echo "$ARGUMENTS" | grep -q -- '--granularity'; then
  GRAN=$(echo "$ARGUMENTS" | sed -n 's/.*--granularity[[:space:]]\+\([^[:space:]]\+\).*/\1/p')
  GRAN="${GRAN:-pr}"
  case "$GRAN" in
    micro|pr|macro) echo "[INFO] Granularity override: $GRAN" ;;
    *) echo "[ERROR] Invalid granularity: '$GRAN'. Must be one of: micro, pr, macro" >&2; exit 1 ;;
  esac
fi
echo "--- Fetching spec issue ---"
spec=$(gh issue view "$ARG" --json number,title,url,body,state,labels 2>/dev/null)
if [ -z "$spec" ] || [ "$spec" = "null" ]; then echo "[ERROR] Issue #$ARG not found"; exit 1; fi
spec_num=$(printf '%s' "$spec" | jq -r '.number')
spec_url=$(printf '%s' "$spec" | jq -r '.url')
spec_label=$(printf '%s' "$spec" | jq -r '[.labels[].name | select(startswith("spec:"))] | .[0]')
echo "[OK] Found spec issue #$spec_num: $spec_url"
spec_body=$(printf '%s' "$spec" | jq -r '.body')
if ! printf '%s' "$spec_body" | grep -q "## Task Breakdown"; then
  echo "[ERROR] No Task Breakdown section found. Run /pm:spec-plan $ARG first."; exit 1
fi
detected=$(printf '%s' "$spec_body" | grep -m1 '<!-- granularity:' | sed 's/.*granularity: *\([^ >]*\).*/\1/')
gran="${GRAN:-${detected:-pr}}"
echo "[INFO] Granularity: $gran | Label: $spec_label"
repo=$(gh repo view --json nameWithOwner -q '.nameWithOwner')
echo "[INFO] Repo: $repo | Spec issue: #$spec_num"
echo ""
echo "--- Existing sub-issues ---"
gh api -H "Accept: application/vnd.github+json" -H "X-GitHub-Api-Version: 2022-11-28" \
  "/repos/$repo/issues/$spec_num/sub_issues" \
  --jq '.[] | "  #\(.number) [\(.state)] \(.title)"' 2>/dev/null || echo "  None"
echo ""
echo "--- Spec issue body ---"
printf '%s\n' "$spec_body"
```

## Instructions

1. **Find and verify the approved plan comment:**

   ```bash
   REPO=$(gh repo view --json nameWithOwner -q '.nameWithOwner')
   # Find the most recent comment with a rocket reaction (marks it as the plan comment)
   COMMENT_ID=$(gh api /repos/$REPO/issues/<spec_issue_number>/comments \
     --jq '[.[] | select(.body | contains("## Task Breakdown"))] | last | .id')

   if [ -z "$COMMENT_ID" ] || [ "$COMMENT_ID" = "null" ]; then
     echo "[ERROR] No plan comment found on issue #<spec_issue_number>. Run /pm:spec-plan first."
     exit 1
   fi

   ROCKET=$(gh api /repos/$REPO/issues/comments/$COMMENT_ID/reactions \
     --jq '[.[] | select(.content == "rocket")] | length')

   if [ "$ROCKET" = "0" ]; then
     echo "[ERROR] Comment #$COMMENT_ID is not a valid plan comment (missing 🚀 reaction)."
     exit 1
   fi

   # Check for +1 (👍) approval reaction
   APPROVED=$(gh api /repos/$REPO/issues/comments/$COMMENT_ID/reactions \
     --jq '[.[] | select(.content == "+1")] | length')

   if [ "$APPROVED" = "0" ]; then
     echo "[ERROR] Plan comment #$COMMENT_ID has not been approved. React with 👍 on the plan comment to approve."
     exit 1
   fi

   echo "[INFO] Plan approved (👍 x$APPROVED). Proceeding with decomposition."
   ```

   If no plan comment exists or it has no 👍 reaction, abort.

2. **Determine task splitting rules** from the granularity reported in preflight:
   - `micro` — Split aggressively. Each task: 0.5–1 day. One concern per task (single endpoint, single component, single migration).
   - `pr` (default) — Keep tasks as PR-sized units. Each task: 1–3 days, ~200–400 LOC, reviewable in under 30 minutes. **Bias toward merging**: combine rows that share the same area or would naturally be reviewed together. Only split a row if it clearly exceeds the LOC/time ceiling on its own. Tests belong in the same task as the code they cover.
   - `macro` — Merge related tasks into milestones. Each task: 3–7 days. Group rows by area. Aim for 3–5 tasks total.

3. **Parse the Task Breakdown table** from the approved plan comment body. Apply the splitting rules to produce the **new task list** (list of titles + tags + dependencies).

4. **Reconcile** against existing sub-issues fetched in preflight:

   - **Match** existing sub-issues to new tasks using semantic similarity — consider two tasks the same if they describe the same intent, even if the title wording differs slightly (e.g. "Set up DB schema" matches "Database schema setup"). Do not rely on exact string comparison.
   - **New tasks** (in new list, no semantically matching sub-issue) → create issue + register as sub-issue.
   - **Orphan tasks** (existing sub-issue, no semantically matching new task) → close issue with comment + remove from sub-issues.
   - **Unchanged tasks** (semantically matched) → skip, already consistent.

   If there are orphans or new tasks to create, show the diff to the user and confirm before proceeding:
   ```
   Reconcile plan:
     + Create: "Task A", "Task B"
     - Close orphan: #42 "Old Task C" (removed from spec)
   Proceed? (yes/no)
   ```

5. **Ensure `task` label exists**:
   ```bash
   gh label create "task" --color "1D76DB" --description "Task from spec" --force 2>/dev/null || true
   ```

6. **Close orphan sub-issues** (removed from spec):

   a. Close the issue with a comment:
   ```bash
   gh issue close <orphan_number> --comment "Closing: this task was removed from spec #<spec_issue_number> during re-decomposition."
   ```

   b. Remove it from the spec issue's sub-issues:
   ```bash
   gh api \
     --method DELETE \
     -H "Accept: application/vnd.github+json" \
     -H "X-GitHub-Api-Version: 2022-11-28" \
     "/repos/$REPO/issues/$SPEC_ISSUE_NUMBER/sub_issues" \
     -F sub_issue_id="<orphan_issue_id>"
   ```

7. **Create new task issues** and register as sub-issues:

   a. Read the task issue template to understand the expected fields:
   ```bash
   TASK_TEMPLATE=".github/ISSUE_TEMPLATE/task.yml"
   if [ -f "$TASK_TEMPLATE" ]; then
     echo "[OK] Using task template: $TASK_TEMPLATE"
     cat "$TASK_TEMPLATE"
   else
     echo "[WARN] No task template found at $TASK_TEMPLATE, using plain body"
   fi
   ```

   b. Write the task body to a temp file and create the issue:
   ```bash
   TASK_BODY_FILE=$(mktemp "${TMPDIR:-/tmp}/codemate-task-body.XXXXXX")
   trap 'rm -f "$TASK_BODY_FILE"' EXIT
   printf '%s' "<body content>" > "$TASK_BODY_FILE"

   TASK_URL=$(gh issue create \
     --title "<task title>" \
     --label "task" \
     --body-file "$TASK_BODY_FILE")
   rm -f "$TASK_BODY_FILE"
   trap - EXIT
   ```

   c. Get the task issue's numeric ID (not number):
   ```bash
   TASK_ISSUE_NUMBER=$(echo "$TASK_URL" | grep -oE '[0-9]+$')
   gh api -X PATCH repos/$REPO/issues/$TASK_ISSUE_NUMBER --field type=Task
   TASK_ISSUE_ID=$(gh api /repos/$REPO/issues/$TASK_ISSUE_NUMBER --jq '.id')
   ```

   d. Register as sub-issue of the spec issue:
   ```bash
   gh api \
     --method POST \
     -H "Accept: application/vnd.github+json" \
     -H "X-GitHub-Api-Version: 2022-11-28" \
     "/repos/$REPO/issues/$SPEC_ISSUE_NUMBER/sub_issues" \
     -F sub_issue_id="$TASK_ISSUE_ID"
   ```

8. **Add `ready` label** to the spec issue:
   ```bash
   gh label create "ready" --color "0075CA" --description "Spec tasks have been decomposed" --force 2>/dev/null || true
   gh issue edit $SPEC_ISSUE_NUMBER --add-label "ready"
   ```

9. **Output a summary:**
   ```
   ✅ Decomposed <n> tasks for spec #<spec_issue_number> (granularity: <value>)

   Created (<n> new):
     #<num> - <title> → <url>

   Closed orphans (<n> removed):
     #<num> - <title>

   Unchanged (<n> skipped):
     #<num> - <title>

   Next steps:
     - View progress: /pm:spec-status <spec_issue_number>
     - Find next task: /pm:spec-next <spec_issue_number>
   ```

## Prerequisites
- A spec issue must exist with a plan comment containing `## Task Breakdown` and a 👍 reaction (run `/pm:spec-plan <issue-number>` first, then approve the comment)
- Must be authenticated: `gh auth status`
- Must be inside a GitHub repository
