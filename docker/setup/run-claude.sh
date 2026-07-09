#!/bin/bash
set -e

SETUP_DIR="$(dirname "$0")"

# Source common utilities
source "$SETUP_DIR/shell/common.sh"

# Claude-specific setup belongs in the Claude runner.
run_setup_script "$SETUP_DIR/python/setup-ccline.py" "Running setup-ccline.py..."
run_setup_script "$SETUP_DIR/shell/setup-claude-plugins.sh" "Running setup-claude-plugins.sh..."

# Configuration
CLAUDE_SESSION="claude-code"

printf "${GREEN}Starting CodeMate with tmux...${RESET}\n"

# Function to check if a tmux session exists
session_exists() {
    tmux has-session -t "$1" 2>/dev/null
}

# Kill existing Claude session if it exists
if session_exists "$CLAUDE_SESSION"; then
    echo "Killing existing Claude Code session..."
    tmux kill-session -t "$CLAUDE_SESSION"
fi

# Start Claude Code in a detached tmux session
printf "${GREEN}Starting Claude Code in tmux session: $CLAUDE_SESSION${RESET}\n"

CLAUDE_COMMAND="claude --dangerously-skip-permissions"
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

    CLAUDE_COMMAND="$CLAUDE_COMMAND --append-system-prompt \"\$(cat $SYSTEM_PROMPT_FILE)\""
fi

tmux new-session -d -s "$CLAUDE_SESSION" "$CLAUDE_COMMAND"

# Send initial query if provided
if [ -n "$CODEMATE_QUERY" ]; then
    printf "${GREEN}Waiting for Claude to initialize...${RESET}\n"
    sleep 5
    printf "${GREEN}Sending initial query to Claude...${RESET}\n"

    # Send command and verify submission with retry mechanism
    send_and_verify_command "$CLAUDE_SESSION" "$CODEMATE_QUERY" 3
else
    sleep 2
fi

# Display session information
printf "${YELLOW}=== CodeMate Sessions ===${RESET}\n"
echo "Claude Code session: $CLAUDE_SESSION (tmux)"
echo "PR Monitor: cron job (every minute)"
echo ""
printf "${YELLOW}=== Log Files ===${RESET}\n"
echo "Monitor log: /tmp/pr-monitor.log"
echo "State file: /tmp/pr-monitor-state"
echo ""
printf "${YELLOW}=== Commands ===${RESET}\n"
echo "View monitor log: tail -f /tmp/pr-monitor.log"
echo "View cron jobs: crontab -l"
echo "List tmux sessions: tmux ls"
echo "Kill Claude: tmux kill-server"
echo ""
printf "${GREEN}Attaching to Claude Code session...${RESET}\n"
sleep 1

# Attach to Claude Code session
tmux attach -t "$CLAUDE_SESSION"
