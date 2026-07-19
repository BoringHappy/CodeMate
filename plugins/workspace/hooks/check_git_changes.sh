#!/usr/bin/env bash
# Check for uncommitted git changes and return a structured Stop decision.

set -uo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=hook_common.sh
source "$SCRIPT_DIR/hook_common.sh"

HOOK_INPUT=$(cat)
WORKSPACE_DIR=$(codemate_workspace_dir "$HOOK_INPUT") || exit 0
BLOCK_COUNTER_FILE="$WORKSPACE_DIR/git-changes-block-count"
MAX_BLOCKS=2

_git_changes_inject() {
    local instruction
    if codemate_is_codex; then
        instruction="the git:commit skill"
    else
        instruction="/git:commit"
    fi
    jq -cn --arg reason "Uncommitted changes remain. Please use $instruction to commit and push them before stopping." \
        '{decision: "block", reason: $reason}'
}

_git_changes_warn() {
    jq -cn --arg message "Uncommitted changes still remain after $MAX_BLOCKS continuation attempts; the session will stop to avoid an infinite hook loop." \
        '{systemMessage: $message}'
}

check_git_changes() {
    local git_changes
    git_changes=$(git status --porcelain 2>/dev/null || echo "")

    if [ -z "$git_changes" ]; then
        # Clean — reset counter
        rm -f "$BLOCK_COUNTER_FILE"
        return
    fi

    # Read current block count
    local count=0
    [ -f "$BLOCK_COUNTER_FILE" ] && count=$(cat "$BLOCK_COUNTER_FILE")

    if [ "$count" -lt "$MAX_BLOCKS" ]; then
        printf '%s\n' "$((count + 1))" > "$BLOCK_COUNTER_FILE"
        _git_changes_inject
    else
        # Exceeded max blocks — warn instead of block to avoid infinite loop
        rm -f "$BLOCK_COUNTER_FILE"
        _git_changes_warn
    fi
    return 0
}

check_git_changes
