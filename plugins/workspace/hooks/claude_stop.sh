#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=hook_common.sh
source "$SCRIPT_DIR/hook_common.sh"

HOOK_INPUT=$(cat)
SESSION_DIR=$(codemate_session_dir "$HOOK_INPUT") || exit 0
EVENT_FINGERPRINT=$(codemate_event_fingerprint "$HOOK_INPUT") || exit 0

exec 9>"$SESSION_DIR/stop.lock"
flock -w 5 9 || exit 0

# The synchronous Stop status handler runs alongside this async handler. Wait
# briefly for it instead of writing Stop here; otherwise a delayed background
# start could overwrite a newer UserPromptSubmit event.
for _ in {1..20}; do
    codemate_session_is_stopped "$SESSION_DIR" "$EVENT_FINGERPRINT" && break
    sleep 0.1
done
codemate_session_is_stopped "$SESSION_DIR" "$EVENT_FINGERPRINT" || exit 0

# asyncRewake wakes Claude only when this process exits 2. Structured output on
# exit 0 remains available for non-blocking UI messages.
git_check_output=$(printf '%s' "$HOOK_INPUT" | "$SCRIPT_DIR/check_git_changes.sh")
if [ -n "$git_check_output" ]; then
    if [ "$(printf '%s' "$git_check_output" | jq -r '.decision // empty')" = "block" ]; then
        printf '%s\n' "$(printf '%s' "$git_check_output" | jq -r '.reason')" >&2
        exit 2
    fi
    printf '%s' "$git_check_output"
    exit 0
fi

printf '%s' "$HOOK_INPUT" | "$SCRIPT_DIR/send_to_slack.sh"
printf '%s' "$HOOK_INPUT" | "$SCRIPT_DIR/send_to_lark.sh"

monitor_output=$(printf '%s' "$HOOK_INPUT" | "$SCRIPT_DIR/monitor_pr.sh")
if [ -n "$monitor_output" ]; then
    printf '%s\n' "$(printf '%s' "$monitor_output" | jq -r '.reason')" >&2
    exit 2
fi

exit 0
