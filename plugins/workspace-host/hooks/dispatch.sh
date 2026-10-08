#!/usr/bin/env bash
set -eu

# This plugin is injected by the native launcher. Reuse the PR lifecycle
# implementation, but omit project service/container setup entirely.
[ "${CODEMATE_MODE:-}" = "host" ] || exit 0
case "${1:-}" in
    record_session_status.sh) ;;
    stop.sh|claude_stop.sh)
        [ -z "${CODEMATE_CHAT:-}" ] || exit 0
        ;;
    *) exit 1 ;;
esac
exec bash "${CODEMATE_WORKSPACE_HOOKS_ROOT:?}/$1"
