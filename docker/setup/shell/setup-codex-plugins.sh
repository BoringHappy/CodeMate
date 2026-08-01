#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

printf "${CYAN}Setting up Codex plugins...${RESET}\n"

# Create tmp directory in .codex to avoid cross-device link errors and keep
# Codex's temp/hook state out of Claude's config directory when both runtimes
# share one host. CODEMATE_TMPDIR (not TMPDIR) keeps CodeMate's own state
# scoped without changing the temp directory of every process in the container.
printf "  Creating temp directory at /home/agent/.codex/tmp\n"
mkdir -p /home/agent/.codex/tmp
export CODEMATE_TMPDIR=/home/agent/.codex/tmp

ALL_MARKETPLACES="${CODEMATE_DEFAULT_MARKETPLACES:-}"
if [ -n "${CODEMATE_CUSTOM_MARKETPLACES:-}" ]; then
    if [ -n "$ALL_MARKETPLACES" ]; then
        ALL_MARKETPLACES="${ALL_MARKETPLACES},${CODEMATE_CUSTOM_MARKETPLACES}"
    else
        ALL_MARKETPLACES="$CODEMATE_CUSTOM_MARKETPLACES"
    fi
fi

if [ -n "$ALL_MARKETPLACES" ]; then
    printf "\n${CYAN}Adding Codex marketplaces:${RESET}\n"
    marketplace_list="$(codex plugin marketplace list --json)"
    IFS=',' read -ra MARKETPLACE_ARRAY <<< "$ALL_MARKETPLACES"

    for marketplace in "${MARKETPLACE_ARRAY[@]}"; do
        marketplace="$(echo "$marketplace" | xargs)"
        [ -z "$marketplace" ] && continue

        if [[ ! "$marketplace" =~ ^[a-zA-Z0-9_-]+/[a-zA-Z0-9_.-]+$ ]]; then
            printf "  ${YELLOW}⚠ Skipping invalid marketplace format: '%s' (expected: owner/repo)${RESET}\n" "$marketplace"
            continue
        fi

        marketplace_name="$(basename "$marketplace" .git | tr '[:upper:]' '[:lower:]')"
        if jq -e --arg name "$marketplace_name" '.marketplaces[] | select(.name == $name)' <<< "$marketplace_list" >/dev/null; then
            printf "  ${GREEN}✓ %s marketplace already configured${RESET}\n" "$marketplace_name"
        else
            printf "  Adding %s marketplace from %s...\n" "$marketplace_name" "$marketplace"
            codex plugin marketplace add "$marketplace"
        fi
    done

    printf "\n${CYAN}Updating Codex marketplaces:${RESET}\n"
    codex plugin marketplace upgrade
fi

ALL_PLUGINS="${CODEMATE_DEFAULT_PLUGINS:-}"
if [ -n "${CODEMATE_CUSTOM_PLUGINS:-}" ]; then
    if [ -n "$ALL_PLUGINS" ]; then
        ALL_PLUGINS="${ALL_PLUGINS},${CODEMATE_CUSTOM_PLUGINS}"
    else
        ALL_PLUGINS="$CODEMATE_CUSTOM_PLUGINS"
    fi
fi

if [ -n "$ALL_PLUGINS" ]; then
    printf "\n${CYAN}Installing or updating Codex plugins:${RESET}\n"
    IFS=',' read -ra PLUGIN_ARRAY <<< "$ALL_PLUGINS"

    for plugin in "${PLUGIN_ARRAY[@]}"; do
        plugin="$(echo "$plugin" | xargs)"
        [ -z "$plugin" ] && continue

        if [[ ! "$plugin" =~ ^[a-zA-Z0-9_-]+@[a-zA-Z0-9_-]+$ ]]; then
            printf "  ${YELLOW}⚠ Skipping invalid plugin format: '%s' (expected: plugin@marketplace)${RESET}\n" "$plugin"
            continue
        fi

        printf "  Installing or updating %s...\n" "$plugin"
        codex plugin add "$plugin"
    done
fi

printf "\n${GREEN}✓ Codex plugin setup complete${RESET}\n"
