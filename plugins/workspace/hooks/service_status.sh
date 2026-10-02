#!/usr/bin/env bash
set -uo pipefail

# SessionStart context for both agents. Only query the service manager: hooks
# never install dependencies, start processes, or read the manager's state files.
HOOK_INPUT=$(cat)
event=$(printf '%s' "$HOOK_INPUT" | jq -er '.hook_event_name // empty' 2>/dev/null) || exit 0
[ "$event" = "SessionStart" ] || exit 0
cwd=$(printf '%s' "$HOOK_INPUT" | jq -er '.cwd | select(type == "string" and length > 0)' 2>/dev/null) || exit 0
workspace=$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null) || exit 0
[ -f "$workspace/.codemate/config.yaml" ] || exit 0
cd "$workspace" || exit 0

if ! command -v codemate-services >/dev/null 2>&1; then
    context="This repository has .codemate/config.yaml, but codemate-services is unavailable in this environment. Automatic project service startup cannot be verified."
elif status=$(timeout 8s codemate-services status --json 2>/dev/null) && \
    printf '%s' "$status" | jq -e 'type == "object" and (.services | type == "array")' >/dev/null 2>&1; then
    # Include status and log locations, never arbitrary application logs or env.
    summary=$(printf '%s' "$status" | jq -c '{setup, startup_error, logs_dir, services: .services[:20], service_count: (.services | length)}')
    context="CodeMate project service status (checked at session start): ${summary:0:16000}
Use codemate-services status --json to refresh status, codemate-services logs <name> --follow to inspect logs, and codemate-services start/restart/stop <name> to manage a service. Setup and startup errors are in setup.log and startup.log under logs_dir."
else
    context="This repository has .codemate/config.yaml, but the service status check failed or timed out. Run codemate-services status --json to diagnose; service readiness is unknown."
fi

jq -n --arg context "$context" '{hookSpecificOutput: {hookEventName: "SessionStart", additionalContext: $context}}'
