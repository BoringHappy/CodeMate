#!/bin/bash
set -e

SETUP_DIR="$(dirname "$0")"

# Source common utilities
source "$SETUP_DIR/shell/common.sh"

# Install or refresh Codex-specific marketplaces and plugins before launch.
run_setup_script "$SETUP_DIR/shell/setup-codex-plugins.sh" "Running setup-codex-plugins.sh..."

printf "${GREEN}Starting CodeMate Codex...${RESET}\n"

# Launch Codex directly on the container TTY. The initial query is passed as a
# native initial prompt (positional argument), so no tmux session is needed.
# CodeMate already isolates Codex inside a disposable container, so Codex can
# operate without a second sandbox or interactive approval prompts.
CODEX_CMD=(codex --dangerously-bypass-approvals-and-sandbox)

if [ -n "$CODEMATE_CHAT" ]; then
    printf "${CYAN}Chat mode enabled; skipping CodeMate Codex instructions${RESET}\n"
else
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

    if [ -f "$SYSTEM_PROMPT_FILE" ]; then
        CODEX_DEVELOPER_INSTRUCTIONS_CONFIG="$(
            python3 - "$SYSTEM_PROMPT_FILE" <<'PY'
import json
import sys
from pathlib import Path

print(f"developer_instructions={json.dumps(Path(sys.argv[1]).read_text())}")
PY
        )"
        CODEX_CMD+=(--config "$CODEX_DEVELOPER_INSTRUCTIONS_CONFIG")
    fi
fi

# Send the initial query natively if provided
if [ -n "$CODEMATE_QUERY" ]; then
    printf "${GREEN}Starting Codex with the initial query...${RESET}\n"
    CODEX_CMD+=("$CODEMATE_QUERY")
fi

printf "${YELLOW}=== CodeMate Session ===${RESET}\n"
echo "Codex session started directly in this terminal"
echo "Detach: Ctrl+P Ctrl+Q · Re-attach: re-run codemate (docker attach)"

exec "${CODEX_CMD[@]}"
