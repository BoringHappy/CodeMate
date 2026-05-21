#!/bin/bash
set -e

SETUP_DIR="/usr/local/bin/setup"

# Source common utilities
source "$SETUP_DIR/shell/common.sh"

# Enforce region restriction before launching Claude.
run_setup_script "$SETUP_DIR/shell/check-region.sh" "Running check-region.sh..."

# Start cron daemon for PR monitoring
printf "${GREEN}Starting cron daemon...${RESET}\n"
sudo service cron start || sudo cron || true

run_setup_script "$SETUP_DIR/shell/setup-git.sh" "Running setup-git.sh..."
run_setup_script "$SETUP_DIR/shell/setup-gh.sh" "Running setup-gh.sh..."
run_setup_script "$SETUP_DIR/python/setup-ccline.py" "Running setup-ccline.py..."
run_setup_script "$SETUP_DIR/shell/setup-plugins.sh" "Running setup-plugins.sh..."
run_setup_script "$SETUP_DIR/python/setup-repo.py" "Running setup-repo.py..."
run_setup_script "$SETUP_DIR/shell/setup-softlinks.sh" "Running setup-softlinks.sh..."

printf "\n${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}\n"
printf "${GREEN}✓ All setup scripts completed successfully${RESET}\n"
printf "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}\n"
exec "$@"

