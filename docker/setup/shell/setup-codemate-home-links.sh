#!/bin/bash

# Source common utilities
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

CODEMATE_HOME="${CODEMATE_HOME:-/home/agent/.codemate}"
AGENT_HOME="${HOME:-/home/agent}"
BACKUP_DIR="$AGENT_HOME/.codemate-link-backup"

if [ ! -d "$CODEMATE_HOME" ]; then
    printf "${BLUE}${CODEMATE_HOME} not mounted, skipping CodeMate home-link setup${RESET}\n"
    exit 0
fi

printf "${YELLOW}Setting up CodeMate home links from ${CODEMATE_HOME}...${RESET}\n"

mkdir -p "$CODEMATE_HOME/.claude" "$CODEMATE_HOME/.cache" "$CODEMATE_HOME/.codex" "$CODEMATE_HOME/.kube"

link_entry() {
    local src="$1"
    local name
    local dst
    local backup

    name="$(basename "$src")"
    dst="$AGENT_HOME/$name"

    if [ "$dst" = "$CODEMATE_HOME" ]; then
        return 0
    fi

    if [ -L "$dst" ]; then
        if [ "$(readlink "$dst")" = "$src" ]; then
            printf "  ${GREEN}✓ Linked${RESET} ${BLUE}${dst}${RESET} → ${BLUE}${src}${RESET}\n"
            return 0
        fi
        rm -f "$dst"
    elif [ -e "$dst" ]; then
        mkdir -p "$BACKUP_DIR"
        backup="$BACKUP_DIR/${name}.$(date +%s)"
        if mv "$dst" "$backup" 2>/dev/null; then
            printf "  ${YELLOW}Moved existing ${dst} to ${backup}${RESET}\n"
        else
            printf "  ${YELLOW}⚠ Skipping ${dst}; existing path could not be moved${RESET}\n"
            return 0
        fi
    fi

    if ln -s "$src" "$dst"; then
        printf "  ${GREEN}✓ Linked${RESET} ${BLUE}${dst}${RESET} → ${BLUE}${src}${RESET}\n"
    else
        printf "  ${RED}✗ Failed to link ${dst} → ${src}${RESET}\n"
    fi
}

while IFS= read -r -d '' entry; do
    link_entry "$entry"
done < <(find "$CODEMATE_HOME" -mindepth 1 -maxdepth 1 -print0 | sort -z)

printf "${GREEN}✓ CodeMate home-link setup completed${RESET}\n"
