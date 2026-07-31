#!/usr/bin/env bash
# Stop hook helper - sends notification to Slack.
# Requires SLACK_WEBHOOK environment variable to be set

set -uo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=hook_common.sh
source "$SCRIPT_DIR/hook_common.sh"

HOOK_INPUT=$(cat)
SESSION_DIR=$(codemate_session_dir "$HOOK_INPUT") || exit 0
EVENT_FINGERPRINT=$(codemate_event_fingerprint "$HOOK_INPUT") || exit 0
WORKSPACE_DIR=$(codemate_workspace_dir "$HOOK_INPUT") || exit 0

# Exit if SLACK_WEBHOOK is not set
[ -z "${SLACK_WEBHOOK:-}" ] && exit 0
codemate_session_is_stopped "$SESSION_DIR" "$EVENT_FINGERPRINT" || exit 0

# Check if there are new commits since session start
COMMIT_FILE="$WORKSPACE_DIR/slack-last-commit"
START_COMMIT_FILE="$WORKSPACE_DIR/start_commit"
if [ ! -f "$COMMIT_FILE" ] && [ -f "$START_COMMIT_FILE" ]; then
    cp "$START_COMMIT_FILE" "$COMMIT_FILE"
fi
if [ -f "$COMMIT_FILE" ]; then
    LAST_NOTIFIED_COMMIT=$(cat "$COMMIT_FILE")
    CURRENT_COMMIT=$(git rev-parse HEAD 2>/dev/null)
    if [ "$LAST_NOTIFIED_COMMIT" = "$CURRENT_COMMIT" ]; then
        # No new commits, skip sending notification
        exit 0
    fi
fi

# Get PR info only when branch-local state says an open PR exists.
PR_INFO=""
if codemate_load_pr_reference && codemate_session_is_stopped "$SESSION_DIR" "$EVENT_FINGERPRINT"; then
    PR_INFO=$(gh pr view "$CODEMATE_CURRENT_PR_NUMBER" --json number,title,url 2>/dev/null || true)
fi
if [ -n "$PR_INFO" ]; then
    PR_URL=$(echo "$PR_INFO" | jq -r '.url // "N/A"')
    PR_TITLE=$(echo "$PR_INFO" | jq -r '.title // "N/A"')
else
    PR_URL="N/A"
    PR_TITLE="N/A"
fi

# Get last commit message
LAST_COMMIT=$(git log -1 --pretty=format:"%s" 2>/dev/null || echo "No commit found")

# Build Slack message payload using Block Kit for rich formatting
PAYLOAD=$(jq -n \
  --arg pr_url "$PR_URL" \
  --arg pr_title "$PR_TITLE" \
  --arg commit "$LAST_COMMIT" \
  '{
    attachments: [
      {
        color: "#36a64f",
        blocks: [
          {
            type: "header",
            text: { type: "plain_text", text: "Code Changes Pushed", emoji: true }
          },
          {
            type: "section",
            text: { type: "mrkdwn", text: ("*PR:* " + $pr_title + "\n*Commit:* " + $commit) }
          },
          {
            type: "actions",
            elements: [
              {
                type: "button",
                text: { type: "plain_text", text: "View PR", emoji: true },
                url: $pr_url,
                style: "primary"
              }
            ]
          }
        ]
      }
    ]
  }')

# Send to Slack webhook
codemate_session_is_stopped "$SESSION_DIR" "$EVENT_FINGERPRINT" || exit 0
curl -s --max-time 10 -X POST -H 'Content-type: application/json' \
    --data "$PAYLOAD" \
    "$SLACK_WEBHOOK" > /dev/null 2>&1

# Update commit file with current commit to avoid duplicate notifications
git rev-parse HEAD 2>/dev/null > "$COMMIT_FILE"

exit 0
