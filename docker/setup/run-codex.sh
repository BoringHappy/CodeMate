#!/bin/bash
set -e

SETUP_DIR="$(dirname "$0")"

# Source common utilities
source "$SETUP_DIR/shell/common.sh"

# Install or refresh Codex-specific marketplaces and plugins before launch.
run_setup_script "$SETUP_DIR/shell/setup-codex-plugins.sh" "Running setup-codex-plugins.sh..."

CODEX_SESSION="codex"

printf "${GREEN}Starting CodeMate Codex with tmux...${RESET}\n"

session_exists() {
    tmux has-session -t "$1" 2>/dev/null
}

if session_exists "$CODEX_SESSION"; then
    echo "Killing existing Codex session..."
    tmux kill-session -t "$CODEX_SESSION"
fi

printf "${GREEN}Starting Codex in tmux session: $CODEX_SESSION${RESET}\n"

# CodeMate already isolates Codex inside a disposable container, so Codex can
# operate without a second sandbox or interactive approval prompts.
CODEX_COMMAND="codex --dangerously-bypass-approvals-and-sandbox"
if [ -n "$CODEMATE_QUERY" ]; then
    printf -v QUOTED_QUERY '%q' "$CODEMATE_QUERY"
    CODEX_COMMAND="$CODEX_COMMAND $QUOTED_QUERY"
fi

tmux new-session -d -s "$CODEX_SESSION" "$CODEX_COMMAND"

if [ -n "$CODEMATE_QUERY" ]; then
    printf "${GREEN}Starting Codex with the initial query...${RESET}\n"
    sleep 2
fi

printf "${YELLOW}=== CodeMate Sessions ===${RESET}\n"
echo "Codex session: $CODEX_SESSION (tmux)"
echo ""
printf "${YELLOW}=== Commands ===${RESET}\n"
echo "List tmux sessions: tmux ls"
echo "Kill Codex: tmux kill-session -t $CODEX_SESSION"
echo ""
printf "${GREEN}Attaching to Codex session...${RESET}\n"
sleep 1

tmux attach -t "$CODEX_SESSION"
