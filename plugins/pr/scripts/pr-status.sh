#!/usr/bin/env bash
#
# Canonical PR-state interface for the CodeMate pr plugin.
#
# GitHub is the source of truth for pull request state. This script resolves the
# open PR for the current Git worktree/branch with a small local cache used only
# for disambiguation and as an offline/eventual-consistency fallback. The cache
# is owned by this plugin and lives under the runtime root -- never inside .git.
#
# Usage:
#   pr-status.sh get                 # resolve current PR; prints JSON or exits 1
#   pr-status.sh set --number N --url U [--branch B]
#   pr-status.sh clear
#
# Output (get): {"number":N,"url":U,"state":"OPEN","branch":B,"source":"github|cache"}
#
# Env:
#   CODEMATE_NO_PR           truthy  => treat as "no PR"
#   CODEMATE_RUNTIME_DIR     runtime root override
#   CODEMATE_TMPDIR          per-agent temp dir (used to derive the root)
#
set -uo pipefail

die() {
    printf 'pr-status: %s\n' "$*" >&2
    exit 2
}

codemate_truthy() {
    case "${1:-}" in
        1|true|TRUE|yes|YES|on|ON) return 0 ;;
        *) return 1 ;;
    esac
}

codemate_runtime_root() {
    local root

    if [ -n "${CODEMATE_RUNTIME_DIR:-}" ]; then
        root="$CODEMATE_RUNTIME_DIR"
    elif [ -n "${CODEMATE_TMPDIR:-}" ]; then
        root="$CODEMATE_TMPDIR/codemate"
    elif [ -n "${XDG_RUNTIME_DIR:-}" ]; then
        root="$XDG_RUNTIME_DIR/codemate"
    else
        root="${TMPDIR:-/tmp}/codemate-$(id -u)"
    fi

    umask 077
    mkdir -p "$root/pr-status"
    chmod 700 "$root" "$root/pr-status" 2>/dev/null || true
    printf '%s\n' "$root"
}

codemate_current_branch() {
    local branch
    branch=$(git branch --show-current 2>/dev/null || true)
    if [ -n "$branch" ]; then
        printf '%s\n' "$branch"
        return 0
    fi
    branch=$(git rev-parse --short=12 HEAD 2>/dev/null) || return 1
    printf 'detached-%s\n' "$branch"
}

codemate_worktree_key() {
    local git_dir branch
    git_dir=$(git rev-parse --absolute-git-dir 2>/dev/null) || return 1
    branch=$(codemate_current_branch) || return 1
    printf '%s\n%s' "$git_dir" "$branch" | sha256sum | awk '{print $1}'
}

codemate_cache_file() {
    local key="$1"
    printf '%s/pr-status/%s.json\n' "$(codemate_runtime_root)" "$key"
}

codemate_legacy_file() {
    local branch="$1"
    printf '%s/codemate/pr-status/%s.json\n' "$(git rev-parse --absolute-git-dir 2>/dev/null)" "$branch"
}

# Prints the gh pr list JSON array for the branch, fork-aware. No output and
# non-zero exit when the query fails.
codemate_pr_list() {
    local branch="$1"

    if git remote get-url upstream >/dev/null 2>&1; then
        local upstream owner
        upstream=$(git remote get-url upstream | sed 's/.*github.com[:/]//' | sed 's/.git$//') || return 1
        owner=$(git remote get-url origin | sed 's/.*github.com[:/]//' | sed 's/.git$//' | cut -d'/' -f1) || return 1
        [ -n "$upstream" ] && [ -n "$owner" ] || return 1
        gh pr list --repo "$upstream" --head "$owner:$branch" \
            --state open --json number,url,state,headRefName 2>/dev/null
    else
        gh pr list --head "$branch" \
            --state open --json number,url,state,headRefName 2>/dev/null
    fi
}

cmd_set() {
    local number="" url="" branch="" key cache dir tmp

    while [ "$#" -gt 0 ]; do
        case "$1" in
            --number) number="${2:-}"; shift 2 ;;
            --url) url="${2:-}"; shift 2 ;;
            --branch) branch="${2:-}"; shift 2 ;;
            *) die "unknown option: $1" ;;
        esac
    done

    [[ "$number" =~ ^[0-9]+$ ]] || die "--number must be a positive integer"
    [ -n "$url" ] || die "--url is required"
    [ -n "$branch" ] || { branch=$(codemate_current_branch) || die "not in a git worktree"; }
    key=$(codemate_worktree_key) || die "not in a git worktree"
    cache=$(codemate_cache_file "$key")
    dir=$(dirname "$cache")
    tmp=$(mktemp "$dir/.pr-status.XXXXXX") || die "mktemp failed"

    jq -n \
        --arg branch "$branch" \
        --arg url "$url" \
        --argjson number "$number" \
        --arg updated_at "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
        '{
            state: "open",
            branch: $branch,
            number: $number,
            url: $url,
            updated_at: $updated_at
        }' > "$tmp"
    mv "$tmp" "$cache"
    printf 'saved %s\n' "$cache" >&2
}

cmd_clear() {
    local key cache
    key=$(codemate_worktree_key) || die "not in a git worktree"
    cache=$(codemate_cache_file "$key")
    rm -f "$cache"
}

cmd_get() {
    local branch key cache pr_json cached_num cached_branch chosen
    local number url state output cached legacy migrated

    codemate_truthy "${CODEMATE_NO_PR:-}" && return 1
    branch=$(codemate_current_branch) || return 1
    key=$(codemate_worktree_key) || return 1
    cache=$(codemate_cache_file "$key")

    # GitHub first: the PR list for this branch is the source of truth.
    pr_json=""
    if command -v gh >/dev/null 2>&1; then
        pr_json=$(codemate_pr_list "$branch") || pr_json=""
    fi
    if [ -n "$pr_json" ] && printf '%s' "$pr_json" | jq -e 'length > 0' >/dev/null 2>&1; then
        # Prefer the cached PR number when it still exists (multiple open PRs
        # on one branch is rare; the cache remembers which one we created).
        chosen=""
        if [ -s "$cache" ]; then
            cached_num=$(jq -r '.number // empty' "$cache" 2>/dev/null)
            cached_branch=$(jq -r '.branch // empty' "$cache" 2>/dev/null)
            if [ -n "$cached_num" ] && [ "$cached_branch" = "$branch" ]; then
                chosen=$(printf '%s' "$pr_json" | jq -c --argjson n "$cached_num" \
                    'map(select(.number == $n)) | .[0] // empty' 2>/dev/null)
            fi
        fi
        [ -n "$chosen" ] || chosen=$(printf '%s' "$pr_json" | jq -c '.[0]' 2>/dev/null)
        number=$(printf '%s' "$chosen" | jq -r '.number // empty' 2>/dev/null)
        url=$(printf '%s' "$chosen" | jq -r '.url // empty' 2>/dev/null)
        state=$(printf '%s' "$chosen" | jq -r '.state // "OPEN"' 2>/dev/null)
        [ -n "$number" ] || return 1
        # Refresh the cache as a side effect.
        cmd_set --number "$number" --url "$url" --branch "$branch" >/dev/null 2>&1 || true
        jq -cn --argjson number "$number" --arg url "$url" --arg state "$state" --arg branch "$branch" \
            '{number: $number, url: $url, state: $state, branch: $branch, source: "github"}'
        return 0
    fi

    # GitHub query failed or empty: fall back to the cache, then the legacy
    # per-worktree status file (written by older pr plugin versions).
    cached=""
    [ -s "$cache" ] && cached=$(cat "$cache")
    if [ -z "$cached" ]; then
        legacy=$(codemate_legacy_file "$branch" 2>/dev/null) || legacy=""
        if [ -n "$legacy" ] && [ -s "$legacy" ]; then
            cached=$(cat "$legacy")
            # Migrate the legacy file into the plugin-owned cache.
            migrated=$(mktemp "$(dirname "$cache")/.pr-status.XXXXXX") 2>/dev/null && {
                printf '%s\n' "$cached" > "$migrated"
                mv "$migrated" "$cache"
                printf 'migrated %s\n' "$legacy" >&2
            } || rm -f "$migrated"
        fi
    fi
    if [ -n "$cached" ] && \
        jq -e --arg b "$branch" \
            '.state == "open" and .branch == $b and (.number | type == "number")' \
            <<<"$cached" >/dev/null 2>&1; then
        output=$(printf '%s' "$cached" | jq -c --arg source "cache" \
            '{number: .number, url: .url, state: .state, branch: .branch, source: $source}' 2>/dev/null)
        [ -n "$output" ] && printf '%s\n' "$output"
        return 0
    fi
    return 1
}

case "${1:-}" in
    get)
        cmd_get
        ;;
    set)
        shift
        cmd_set "$@"
        ;;
    clear)
        cmd_clear
        ;;
    "")
        die "missing subcommand (get|set|clear)"
        ;;
    *)
        die "unknown subcommand: $1 (expected get|set|clear)"
        ;;
esac
