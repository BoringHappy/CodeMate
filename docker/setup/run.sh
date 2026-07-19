#!/bin/bash
set -e

SETUP_DIR="$(dirname "$0")"
CODEMATE_AGENT="${CODEMATE_AGENT:-claude}"
export CODEMATE_AGENT

case "$CODEMATE_AGENT" in
    claude)
        AGENT_RUNNER="$SETUP_DIR/run-claude.sh"
        ;;
    codex)
        AGENT_RUNNER="$SETUP_DIR/run-codex.sh"
        ;;
    *)
        echo "Unsupported CODEMATE_AGENT: ${CODEMATE_AGENT} (expected claude or codex)" >&2
        exit 1
        ;;
esac

source "$SETUP_DIR/shell/common.sh"

# Identify this runtime independently from the agent's own session ID. Hook
# state keys by instance, agent, session, and workspace so multiple agent
# processes can safely share one host or container.
CODEMATE_INSTANCE_ID="${CODEMATE_INSTANCE_ID:-${CODEMATE_AGENT}-$(hostname)-$$}"
export CODEMATE_INSTANCE_ID

exec "$AGENT_RUNNER"
