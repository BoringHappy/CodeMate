#!/bin/bash
set -e

SETUP_DIR="$(dirname "$0")"

# Source common utilities
source "$SETUP_DIR/shell/common.sh"

# Claude-specific setup belongs in the Claude runner.
run_setup_script "$SETUP_DIR/python/setup-ccline.py" "Running setup-ccline.py..."
run_setup_script "$SETUP_DIR/shell/setup-claude-plugins.sh" "Running setup-claude-plugins.sh..."

printf "${GREEN}Starting CodeMate Claude Code...${RESET}\n"

# Launch Claude Code directly on the container TTY. The initial query is passed
# as a native initial prompt (positional argument), so no tmux session or
# send-keys keystroke injection is needed.
CLAUDE_CMD=(claude --dangerously-skip-permissions)

if [ -n "$CODEMATE_CHAT" ]; then
    printf "${CYAN}Chat mode enabled; skipping CodeMate system prompt${RESET}\n"
else
    # Choose system prompt based on workflow type
    if [ -n "$CODEMATE_UPSTREAM_REPO_URL" ]; then
        # Open-source workflow: use opensource system prompt
        SYSTEM_PROMPT_FILE="$SETUP_DIR/prompt/system_prompt_opensource.txt"
        printf "${CYAN}Using open-source workflow system prompt${RESET}\n"
    else
        # Standard workflow: use default system prompt
        SYSTEM_PROMPT_FILE="$SETUP_DIR/prompt/system_prompt.txt"
        printf "${CYAN}Using standard workflow system prompt${RESET}\n"
    fi

    CLAUDE_CMD+=(--append-system-prompt "$(cat "$SYSTEM_PROMPT_FILE")")
fi

# Send the initial query natively if provided
if [ -n "$CODEMATE_QUERY" ]; then
    printf "${GREEN}Starting Claude Code with the initial query...${RESET}\n"
    CLAUDE_CMD+=("$CODEMATE_QUERY")
fi

# Display session information
printf "${YELLOW}=== CodeMate Session ===${RESET}\n"
echo "Claude Code session started directly in this terminal"
echo "PR Monitor: workspace Stop hook (10/30/60/120 second backoff)"
echo "Detach: Ctrl+P Ctrl+Q · Re-attach: re-run codemate (docker attach)"

exec "${CLAUDE_CMD[@]}"
