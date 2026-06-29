#!/bin/bash
set -e

SETUP_DIR="/usr/local/bin/setup"

# Source common utilities
source "$SETUP_DIR/shell/common.sh"
source "$SETUP_DIR/shell/setup-common.sh"

# Claude-specific ccline and plugin setup remains in setup.sh.
run_common_setup "$SETUP_DIR"

printf "\n${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}\n"
printf "${GREEN}✓ Codex setup completed successfully${RESET}\n"
printf "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}\n"
exec "$@"
