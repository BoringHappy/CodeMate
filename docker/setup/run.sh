#!/bin/bash
set -e

# Source common utilities
source "$(dirname "$0")/shell/common.sh"

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

# Choose system prompt based on workflow type
if [ -n "$UPSTREAM_REPO_URL" ]; then
    # Open-source workflow: use opensource system prompt
    SYSTEM_PROMPT_FILE="/usr/local/bin/setup/prompt/system_prompt_opensource.txt"
    printf "${CYAN}Using open-source workflow system prompt${RESET}\n"
else
    # Standard workflow: use default system prompt
    SYSTEM_PROMPT_FILE="/usr/local/bin/setup/prompt/system_prompt.txt"
    printf "${CYAN}Using standard workflow system prompt${RESET}\n"
fi

tmux new-session -d -s "$CLAUDE_SESSION" "claude --dangerously-skip-permissions --append-system-prompt \"\$(cat $SYSTEM_PROMPT_FILE)\""

# Send initial query if provided
if [ -n "$QUERY" ]; then
    printf "${GREEN}Waiting for Claude to initialize...${RESET}\n"
    sleep 5
    printf "${GREEN}Sending initial query to Claude...${RESET}\n"

    # Send command and verify submission with retry mechanism
    send_and_verify_command "$CLAUDE_SESSION" "$QUERY" 3
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
