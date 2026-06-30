#!/bin/bash

# Source common utilities
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

if [ -z "$CODEMATE_GIT_USER_NAME" ]; then
    printf "${RED}Error: CODEMATE_GIT_USER_NAME environment variable is required${RESET}\n"
    exit 1
fi

if [ -z "$CODEMATE_GIT_USER_EMAIL" ]; then
    printf "${RED}Error: CODEMATE_GIT_USER_EMAIL environment variable is required${RESET}\n"
    exit 1
fi

printf "${YELLOW}Setting up git config...${RESET}\n"

printf "  Setting git user.name: ${BLUE}$CODEMATE_GIT_USER_NAME${RESET}\n"
git config --global user.name "$CODEMATE_GIT_USER_NAME"

printf "  Setting git user.email: ${BLUE}$CODEMATE_GIT_USER_EMAIL${RESET}\n"
git config --global user.email "$CODEMATE_GIT_USER_EMAIL"

printf "${GREEN}✓ Git config setup completed successfully${RESET}\n"
