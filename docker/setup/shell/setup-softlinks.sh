#!/bin/bash

# Source common utilities
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

if [ -z "$CODEMATE_SOFT_LINKS" ]; then
    printf "${BLUE}CODEMATE_SOFT_LINKS not set, skipping soft-link setup${RESET}\n"
    exit 0
fi

printf "${YELLOW}Setting up soft links from CODEMATE_SOFT_LINKS...${RESET}\n"

IFS=',' read -ra LINK_PAIRS <<< "$CODEMATE_SOFT_LINKS"
for pair in "${LINK_PAIRS[@]}"; do
    pair="${pair#"${pair%%[![:space:]]*}"}"
    pair="${pair%"${pair##*[![:space:]]}"}"
    [ -z "$pair" ] && continue

    src="${pair%%:*}"
    dst="${pair#*:}"

    if [ -z "$src" ] || [ -z "$dst" ] || [ "$src" = "$pair" ]; then
        printf "  ${YELLOW}⚠ Skipping malformed entry (expected src:dst): ${pair}${RESET}\n"
        continue
    fi

    if [ ! -e "$src" ] && [ ! -L "$src" ]; then
        printf "  ${YELLOW}⚠ Source does not exist: ${src} (linking anyway)${RESET}\n"
    fi

    parent_dir="$(dirname "$dst")"
    if [ ! -d "$parent_dir" ]; then
        mkdir -p "$parent_dir"
    fi

    if ln -sfn "$src" "$dst"; then
        printf "  ${GREEN}✓ Linked${RESET} ${BLUE}${dst}${RESET} → ${BLUE}${src}${RESET}\n"
    else
        printf "  ${RED}✗ Failed to link ${dst} → ${src}${RESET}\n"
    fi
done

printf "${GREEN}✓ Soft-link setup completed${RESET}\n"
