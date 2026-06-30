#!/bin/bash
set -e

# Source common utilities
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

printf "${CYAN}Setting up Claude Code plugins...${RESET}\n"

# Create tmp directory in .claude to avoid cross-device link errors
printf "  Creating temp directory at /home/agent/.claude/tmp\n"
mkdir -p /home/agent/.claude/tmp
export TMPDIR=/home/agent/.claude/tmp

# Combine default and custom marketplaces
ALL_MARKETPLACES="${CODEMATE_DEFAULT_MARKETPLACES}"
if [ -n "$CODEMATE_CUSTOM_MARKETPLACES" ]; then
    if [ -n "$ALL_MARKETPLACES" ]; then
        ALL_MARKETPLACES="${ALL_MARKETPLACES},${CODEMATE_CUSTOM_MARKETPLACES}"
    else
        ALL_MARKETPLACES="${CODEMATE_CUSTOM_MARKETPLACES}"
    fi
fi

# Add all marketplaces (functions check if already added)
if [ -n "$ALL_MARKETPLACES" ]; then
    printf "\n${CYAN}Adding marketplaces:${RESET}\n"
    IFS=',' read -ra MARKETPLACE_ARRAY <<< "$ALL_MARKETPLACES"
    marketplace_count=${#MARKETPLACE_ARRAY[@]}
    marketplace_index=1
    for marketplace in "${MARKETPLACE_ARRAY[@]}"; do
        # Trim whitespace
        marketplace=$(echo "$marketplace" | xargs)
        if [ -n "$marketplace" ]; then
            # Validate marketplace format (should be owner/repo)
            if [[ ! "$marketplace" =~ ^[a-zA-Z0-9_-]+/[a-zA-Z0-9_.-]+$ ]]; then
                printf "  ${YELLOW}⚠ Skipping invalid marketplace format: '$marketplace' (expected: owner/repo)${RESET}\n"
                continue
            fi
            # Extract marketplace name from path (e.g., "username/repo" -> "repo")
            marketplace_name=$(echo "$marketplace" | sed 's|.*/||')
            add_marketplace "$marketplace_index/$marketplace_count" "$marketplace_name" "$marketplace"
            marketplace_index=$((marketplace_index + 1))
        fi
    done
fi

# Update marketplaces to fetch latest plugin information
printf "\n${CYAN}Updating marketplaces:${RESET}\n"
update_marketplaces

# Combine default and custom plugins
ALL_PLUGINS="${CODEMATE_DEFAULT_PLUGINS}"
if [ -n "$CODEMATE_CUSTOM_PLUGINS" ]; then
    if [ -n "$ALL_PLUGINS" ]; then
        ALL_PLUGINS="${ALL_PLUGINS},${CODEMATE_CUSTOM_PLUGINS}"
    else
        ALL_PLUGINS="${CODEMATE_CUSTOM_PLUGINS}"
    fi
fi

# Install and update all plugins
if [ -n "$ALL_PLUGINS" ]; then
    printf "\n${CYAN}Installing and updating plugins:${RESET}\n"
    IFS=',' read -ra PLUGIN_ARRAY <<< "$ALL_PLUGINS"
    plugin_count=${#PLUGIN_ARRAY[@]}
    plugin_index=1
    for plugin in "${PLUGIN_ARRAY[@]}"; do
        # Trim whitespace
        plugin=$(echo "$plugin" | xargs)
        if [ -n "$plugin" ]; then
            # Validate plugin format (should be plugin@marketplace)
            if [[ ! "$plugin" =~ ^[a-zA-Z0-9_-]+@[a-zA-Z0-9_-]+$ ]]; then
                printf "  ${YELLOW}⚠ Skipping invalid plugin format: '$plugin' (expected: plugin@marketplace)${RESET}\n"
                continue
            fi
            install_and_update_plugin "$plugin_index/$plugin_count" "$plugin" ""
            plugin_index=$((plugin_index + 1))
        fi
    done
fi

printf "\n${GREEN}✓ Plugin setup complete${RESET}\n"
