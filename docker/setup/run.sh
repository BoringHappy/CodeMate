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

printf "${GREEN}Starting cron daemon...${RESET}\n"
sudo service cron start || sudo cron || true

exec "$AGENT_RUNNER"
