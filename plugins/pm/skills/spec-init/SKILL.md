---
name: spec-init
description: Starts a guided brainstorming session to create a new spec as a GitHub Issue. Use when the user wants to define requirements for a new feature before writing any code.
---

# Spec Init

Creates a new feature spec as a GitHub Issue through a guided requirements session.

Usage: `/pm:spec-init <short-title>`

## Preflight

!```bash
if [ -z "$ARGUMENTS" ]; then
  echo "[ERROR] No title provided. Usage: /pm:spec-init <short-title>"
  exit 1
fi
```

!```bash
gh repo view --json nameWithOwner -q '"Repo: \(.nameWithOwner)"'
```

!```bash
if [ -f ".github/ISSUE_TEMPLATE/spec.yml" ]; then
  echo "--- Spec issue template ---"
  cat ".github/ISSUE_TEMPLATE/spec.yml"
else
  echo "[WARN] No spec template found at .github/ISSUE_TEMPLATE/spec.yml"
fi
```

## Instructions

1. **Run a discovery session** — ask the user focused questions aligned to the spec template fields. Cover:
   - What problem does this solve and why does it matter?
   - Who are the users? What do they want to achieve? (use "As a / I want / So that" format)
   - What are the measurable acceptance criteria that define done at the feature level?
   - What is explicitly out of scope?
   - What are the dependencies or constraints?

2. **Create the spec issue** — write the body to a temp file, then create the issue. If a spec template was found in preflight, mirror its section headings exactly. Otherwise use the default format below:

   ```bash
   SPEC_BODY_FILE=$(mktemp "${TMPDIR:-/tmp}/codemate-spec-body.XXXXXX")
   trap 'rm -f "$SPEC_BODY_FILE"' EXIT
   printf '%s' "<body content>" > "$SPEC_BODY_FILE"

   SPEC_URL=$(gh issue create \
     --title "[Spec]: $ARGUMENTS" \
     --label "spec" \
     --body-file "$SPEC_BODY_FILE")
   rm -f "$SPEC_BODY_FILE"
   trap - EXIT
   SPEC_NUMBER=$(echo "$SPEC_URL" | grep -oE '[0-9]+$')
   REPO=$(gh repo view --json nameWithOwner -q '.nameWithOwner')
   gh api -X PATCH repos/$REPO/issues/$SPEC_NUMBER --field type=Spec
   ```

   Default body format (used when no template exists):

   ```
   ## Problem Statement
   <what problem this solves and why it matters>

   ## User Stories
   - As a <persona>, I want to <action> so that <outcome>

   ## Acceptance Criteria
   - [ ] <measurable outcome 1>
   - [ ] <measurable outcome 2>

   ## Out of Scope
   - <explicit exclusions>

   ## Dependencies & Constraints
   - <external services, team dependencies, technical constraints>
   ```

3. Confirm: "✅ Spec issue created: `[Spec]: $ARGUMENTS` → <issue_url>"
4. Suggest next step: "Ready to plan the implementation? Run: `/pm:spec-plan <issue_number>`"

## Prerequisites
- Title must be provided as argument
- Must be inside a GitHub repository
