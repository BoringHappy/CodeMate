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

printf "\n${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}\n"
printf "${GREEN}✓ All setup scripts completed successfully${RESET}\n"
printf "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}\n"
exec "$@"
