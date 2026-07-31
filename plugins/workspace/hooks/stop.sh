#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=hook_common.sh
source "$SCRIPT_DIR/hook_common.sh"

HOOK_INPUT=$(cat)
SESSION_DIR=$(codemate_session_dir "$HOOK_INPUT") || exit 0

# A single dispatcher keeps Stop actions ordered. Codex launches matching hook
# handlers concurrently, so separate handlers could otherwise race while
# updating status, notification baselines, or monitor cursors.
exec 9>"$SESSION_DIR/stop.lock"
flock -w 5 9 || exit 0

codemate_record_session_status "$HOOK_INPUT" || exit 0

git_check_output=$(printf '%s' "$HOOK_INPUT" | "$SCRIPT_DIR/check_git_changes.sh")
if [ -n "$git_check_output" ]; then
    printf '%s' "$git_check_output"
    exit 0
fi

printf '%s' "$HOOK_INPUT" | "$SCRIPT_DIR/send_to_slack.sh"
printf '%s' "$HOOK_INPUT" | "$SCRIPT_DIR/send_to_lark.sh"

# monitor_pr.sh emits either no output (allow Stop) or one structured Stop
# continuation decision. Keep stdout otherwise empty because Stop hooks require
# JSON output when they return content.
printf '%s' "$HOOK_INPUT" | "$SCRIPT_DIR/monitor_pr.sh"
