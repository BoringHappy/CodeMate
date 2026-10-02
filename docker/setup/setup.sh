#!/bin/bash
set -e

SETUP_DIR="/usr/local/bin/setup"

# Source common utilities
source "$SETUP_DIR/shell/common.sh"

run_setup_script "$SETUP_DIR/shell/setup-git.sh" "Running setup-git.sh..."
run_setup_script "$SETUP_DIR/shell/setup-gh.sh" "Running setup-gh.sh..."
run_setup_script "$SETUP_DIR/python/setup-repo.py" "Running setup-repo.py..."
run_setup_script "$SETUP_DIR/shell/setup-precommit.sh" "Running setup-precommit.sh..."
run_setup_script "$SETUP_DIR/shell/setup-softlinks.sh" "Running setup-softlinks.sh..."

# Project services run independently in the background. Startup failures should
# still allow a session to open so the project configuration can be repaired.
if ! /usr/bin/python3 "$SETUP_DIR/python/project-services.py" up; then
    printf "${YELLOW}Project service startup failed; continuing to the session for repairs.${RESET}\n"
fi

printf "\n${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}\n"
printf "${GREEN}Starting CodeMate session${RESET}\n"
printf "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}\n"
exec "$@"
