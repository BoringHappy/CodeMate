#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=hook_common.sh
source "$SCRIPT_DIR/hook_common.sh"

HOOK_INPUT=$(cat)
codemate_record_session_status "$HOOK_INPUT" || exit 0

exit 0
