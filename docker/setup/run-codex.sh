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

# Choose additional Codex instructions based on workflow type.
if [ -n "$CODEMATE_UPSTREAM_REPO_URL" ]; then
    # Open-source workflow: use opensource system prompt
    SYSTEM_PROMPT_FILE="$SETUP_DIR/prompt/system_prompt_opensource.txt"
    printf "${CYAN}Using open-source workflow Codex instructions${RESET}\n"
else
    # Standard workflow: use default system prompt
    SYSTEM_PROMPT_FILE="$SETUP_DIR/prompt/system_prompt.txt"
    printf "${CYAN}Using standard workflow Codex instructions${RESET}\n"
fi

# CodeMate already isolates Codex inside a disposable container, so Codex can
# operate without a second sandbox or interactive approval prompts.
CODEX_COMMAND="codex --dangerously-bypass-approvals-and-sandbox"
if [ -f "$SYSTEM_PROMPT_FILE" ]; then
    CODEX_DEVELOPER_INSTRUCTIONS_CONFIG="$(
        python3 - "$SYSTEM_PROMPT_FILE" <<'PY'
import json
import sys
from pathlib import Path

print(f"developer_instructions={json.dumps(Path(sys.argv[1]).read_text())}")
PY
    )"
    printf -v QUOTED_CONFIG '%q' "$CODEX_DEVELOPER_INSTRUCTIONS_CONFIG"
    CODEX_COMMAND="$CODEX_COMMAND --config $QUOTED_CONFIG"
fi

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
