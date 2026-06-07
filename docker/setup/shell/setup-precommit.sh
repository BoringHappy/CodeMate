#!/bin/bash

# Source common utilities
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

# Determine workspace path the same way setup-repo.py does:
# /home/agent/<repo_name>, where <repo_name> is derived from GIT_REPO_URL.
if [ -z "$GIT_REPO_URL" ]; then
    printf "${BLUE}GIT_REPO_URL not set, skipping pre-commit setup${RESET}\n"
    exit 0
fi

repo_url="${GIT_REPO_URL%.git}"
repo_url="${repo_url%/}"
repo_name="${repo_url##*/}"
workspace="/home/agent/${repo_name}"

config_file="$workspace/.pre-commit-config.yaml"

if [ ! -f "$config_file" ]; then
    printf "${BLUE}No .pre-commit-config.yaml found in ${workspace}, skipping pre-commit setup${RESET}\n"
    exit 0
fi

printf "${YELLOW}Setting up pre-commit hooks...${RESET}\n"

# Ensure the pre-commit CLI is available, installing it if necessary.
if ! command -v pre-commit >/dev/null 2>&1; then
    printf "  Installing pre-commit...\n"
    if command -v uv >/dev/null 2>&1 && uv tool install pre-commit 2>&1; then
        printf "  ${GREEN}✓ pre-commit installed via uv${RESET}\n"
    elif pip3 install --user pre-commit 2>&1; then
        printf "  ${GREEN}✓ pre-commit installed via pip${RESET}\n"
    else
        printf "  ${RED}✗ Failed to install pre-commit, skipping setup${RESET}\n"
        exit 0
    fi
fi

if ! command -v pre-commit >/dev/null 2>&1; then
    printf "  ${RED}✗ pre-commit not on PATH after install, skipping setup${RESET}\n"
    exit 0
fi

cd "$workspace" || exit 0

printf "  Installing git hooks from ${BLUE}$(basename "$config_file")${RESET}\n"
if pre-commit install 2>&1; then
    printf "${GREEN}✓ Pre-commit hooks installed successfully${RESET}\n"
else
    printf "${YELLOW}⚠ Failed to install pre-commit hooks${RESET}\n"
fi
