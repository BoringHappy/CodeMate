---
name: spec-plan
description: Converts a spec GitHub Issue into a technical implementation plan by appending architecture decisions, tech approach, and a task breakdown. Use after spec-init to turn requirements into an engineering plan. Accepts an optional --granularity flag (micro | pr | macro) to control task sizing.
argument-hint: <issue-number> [--granularity micro|pr|macro]
---

# Spec Plan

Fetches the spec GitHub Issue and appends a technical implementation plan to it.

Usage: `/pm:spec-plan <issue-number> [--granularity micro|pr|macro]`

## Preflight

!```bash
if [ -z "$ARGUMENTS" ]; then
  echo "[ERROR] No issue number provided. Usage: /pm:spec-plan <issue-number> [--granularity micro|pr|macro]"
  exit 1
fi
```

!```bash
GRANULARITY=$(echo "$ARGUMENTS" | sed -n 's/.*--granularity[[:space:]]\+\([^[:space:]]\+\).*/\1/p')
GRANULARITY="${GRANULARITY:-pr}"
case "$GRANULARITY" in
  micro|pr|macro) echo "[INFO] Granularity: $GRANULARITY" ;;
  *) echo "[ERROR] Invalid granularity: '$GRANULARITY'. Must be one of: micro, pr, macro" >&2; exit 1 ;;
esac
```

!```bash
ARG=$(echo "$ARGUMENTS" | awk '{print $1}')
echo "--- Fetching spec issue ---"
spec=$(gh issue view "$ARG" --json number,title,url,body,state 2>/dev/null)
if [ -z "$spec" ] || [ "$spec" = "null" ]; then echo "[ERROR] Issue #$ARG not found"; exit 1; fi
printf '%s' "$spec" | jq -r '"[OK] Found spec issue #\(.number): \(.url)"'
if printf '%s' "$spec" | jq -r '.body' | grep -q "## Architecture Decisions"; then
  echo "[WARN] Plan sections already exist in spec issue"
else
  echo "[OK] Ready to plan"
fi
echo ""
echo "--- Current spec issue body ---"
printf '%s' "$spec" | jq -r '.body'
```

## Instructions

1. **If plan sections already exist** (detected above), ask: "Technical plan already exists in this spec issue. Overwrite? (yes/no)". If yes, remove existing plan sections from the body before proceeding.

2. **Determine task sizing rules** from the granularity reported in preflight:
   - `micro` — Tasks are small and focused. Each task: 0.5–1 day. Aim for 10–20 tasks. Split by thin vertical slice (one endpoint, one component, one migration). Each task should be committable in isolation.
   - `pr` (default) — Tasks map to one logical change: a single coherent unit of functionality that is independently reviewable and mergeable. Each task: 1–3 days, ~200–400 LOC, reviewable in under 30 minutes. Aim for 3–6 tasks. Tests for the change are included in the same task — do not split them out. Do not mix features with bug fixes; exclude unrelated refactoring into separate tasks.
   - `macro` — Tasks are large milestones. Each task: 3–7 days. Aim for 3–5 tasks. Each task represents a major deliverable (e.g. "complete data layer", "end-to-end API", "full UI flow").

3. **Analyze the spec** above and produce a technical plan covering:
   - Architecture decisions (patterns, data models, key choices and their rationale)
   - Tech approach broken down by area (e.g. DB, API, UI, infra)
   - Task breakdown sized according to the granularity rules above. Each task needs:
     - A short title
     - Tags (e.g. data, api, ui, infra — can be multiple)
     - An effort estimate in days consistent with the chosen granularity
     - What it depends on (if anything)

4. **Present the draft plan** in the conversation (do not write to the issue yet). Ask: "Does this plan look good, or would you like to discuss or adjust anything before I update the issue?"
   - If the user requests changes, revise the plan in the conversation and re-present it. Repeat until confirmed.
   - Only proceed to the next step once the user explicitly approves.

5. **Post the plan as a comment** on the spec issue:

   ```bash
   PLAN_BODY_FILE=$(mktemp "${TMPDIR:-/tmp}/codemate-spec-plan.XXXXXX")
   trap 'rm -f "$PLAN_BODY_FILE"' EXIT
   printf '%s' "<plan sections>" > "$PLAN_BODY_FILE"
   COMMENT_URL=$(gh issue comment <spec_issue_number> --body-file "$PLAN_BODY_FILE")
   rm -f "$PLAN_BODY_FILE"
   trap - EXIT
   REPO=$(gh repo view --json nameWithOwner -q '.nameWithOwner')
   COMMENT_ID=$(echo "$COMMENT_URL" | grep -oE '[0-9]+$')
   gh api /repos/$REPO/issues/comments/$COMMENT_ID/reactions --method POST -f content="rocket"
   ```

   The comment body contains only the plan sections (Architecture Decisions, Technical Approach, Task Breakdown, Effort Estimate). The 🚀 reaction is added automatically to mark it as the plan comment.

   ```markdown

   ## Architecture Decisions
   - <decision>: <rationale>

   ## Technical Approach

   ### Data Layer
   <schema, models, migrations>

   ### Service / API Layer
   <endpoints, business logic, integrations>

   ### UI Layer
   <components, pages, interactions — omit if not applicable>

   ### Infrastructure
   <deployment, config, observability — omit if not applicable>

   ## Task Breakdown
   <!-- granularity: <micro|pr|macro> -->
   | # | Title | Tags | Estimate | Depends On |
   |---|-------|------|----------|------------|
   | 1 | <title> | <tags> | <Xd> | — |
   | 2 | <title> | <tags> | <Xd> | 1 |

   ## Effort Estimate
   - Granularity: <micro|pr|macro>
   - Total tasks: <n>
   - Estimated days: <sum of task estimates>
   - Critical path: <longest dependency chain>
   ```

6. **Add `planned` label** to the spec issue:
   ```bash
   gh label create "planned" --color "FBCA04" --description "Spec has a technical plan" --force 2>/dev/null || true
   gh issue edit <spec_issue_number> --add-label "planned"
   ```

7. Confirm: "✅ Plan posted as a comment on spec issue #<number>. React with 👍 to approve, then run `/pm:spec-decompose <number>` to create tasks."

## Prerequisites
- A spec issue must exist (run `/pm:spec-init <title>` first)
- Must be authenticated: `gh auth status`
