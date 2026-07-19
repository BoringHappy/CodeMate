#!/usr/bin/env bash

# Shared state helpers for workspace lifecycle hooks. Runtime state is scoped by
# the agent-provided session ID, while PR state is scoped by Git worktree and
# branch. This keeps concurrent repositories, worktrees, and agent sessions from
# reading or overwriting one another's coordination files.

codemate_runtime_root() {
    local root

    if [ -n "${CODEMATE_RUNTIME_DIR:-}" ]; then
        root="$CODEMATE_RUNTIME_DIR"
    elif [ -n "${XDG_RUNTIME_DIR:-}" ]; then
        root="$XDG_RUNTIME_DIR/codemate"
    else
        root="${TMPDIR:-/tmp}/codemate-$(id -u)"
    fi

    umask 077
    mkdir -p "$root/sessions"
    chmod 700 "$root" "$root/sessions" 2>/dev/null || true
    printf '%s\n' "$root"
}

codemate_is_codex() {
    if [ "${CODEMATE_AGENT:-}" = "codex" ]; then
        return 0
    fi
    if [ -z "${CODEMATE_AGENT:-}" ] && [ -n "${PLUGIN_ROOT:-}" ]; then
        return 0
    fi
    return 1
}

codemate_agent_name() {
    if codemate_is_codex; then
        printf 'codex\n'
    else
        printf 'claude\n'
    fi
}

codemate_safe_component() {
    local value="$1"

    if [[ "$value" =~ ^[A-Za-z0-9._-]+$ ]]; then
        printf '%s\n' "$value"
    else
        printf '%s' "$value" | sha256sum | awk '{print $1}'
    fi
}

codemate_session_id() {
    local input="$1"
    printf '%s' "$input" | jq -er '.session_id | select(type == "string" and length > 0)' 2>/dev/null
}

codemate_session_dir() {
    local input="$1"
    local session_id agent instance_id safe_session safe_agent safe_instance root

    session_id=$(codemate_session_id "$input") || return 1
    agent=$(codemate_agent_name)
    instance_id="${CODEMATE_INSTANCE_ID:-default}"
    safe_session=$(codemate_safe_component "$session_id")
    safe_agent=$(codemate_safe_component "$agent")
    safe_instance=$(codemate_safe_component "$instance_id")
    root=$(codemate_runtime_root) || return 1

    umask 077
    mkdir -p "$root/sessions/${safe_instance}-${safe_agent}-${safe_session}"
    printf '%s\n' "$root/sessions/${safe_instance}-${safe_agent}-${safe_session}"
}

codemate_workspace_dir() {
    local input="$1"
    local session_dir cwd git_dir branch workspace_key workspace_dir metadata_tmp

    session_dir=$(codemate_session_dir "$input") || return 1
    cwd=$(printf '%s' "$input" | jq -er '.cwd | select(type == "string" and length > 0)' 2>/dev/null) || return 1
    git_dir=$(git -C "$cwd" rev-parse --absolute-git-dir 2>/dev/null) || return 1
    branch=$(git -C "$cwd" branch --show-current 2>/dev/null || true)
    if [ -z "$branch" ]; then
        branch=$(git -C "$cwd" rev-parse --short=12 HEAD 2>/dev/null) || return 1
        branch="detached-$branch"
    fi
    workspace_key=$(printf '%s\n%s' "$git_dir" "$branch" | sha256sum | awk '{print $1}')
    workspace_dir="$session_dir/workspaces/$workspace_key"

    umask 077
    mkdir -p "$workspace_dir"
    if [ ! -s "$workspace_dir/workspace.json" ]; then
        metadata_tmp=$(mktemp "$workspace_dir/.workspace.XXXXXX") || return 1
        jq -n --arg git_dir "$git_dir" --arg branch "$branch" \
            '{git_dir: $git_dir, branch: $branch}' > "$metadata_tmp"
        mv "$metadata_tmp" "$workspace_dir/workspace.json"
    fi
    printf '%s\n' "$workspace_dir"
}

codemate_record_session_status() {
    local input="$1"
    local session_dir session_id event cwd branch updated_at tmp current_commit workspace_dir

    session_dir=$(codemate_session_dir "$input") || return 1
    session_id=$(codemate_session_id "$input") || return 1
    event=$(printf '%s' "$input" | jq -r '.hook_event_name // "Unknown"')
    cwd=$(printf '%s' "$input" | jq -r '.cwd // ""')
    updated_at=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
    branch=""
    if [ -n "$cwd" ]; then
        branch=$(git -C "$cwd" branch --show-current 2>/dev/null || true)
    fi

    tmp=$(mktemp "$session_dir/.status.XXXXXX") || return 1
    jq -n \
        --arg session_id "$session_id" \
        --arg instance_id "${CODEMATE_INSTANCE_ID:-}" \
        --arg agent "$(codemate_agent_name)" \
        --arg event "$event" \
        --arg cwd "$cwd" \
        --arg branch "$branch" \
        --arg updated_at "$updated_at" \
        '{
            session_id: $session_id,
            instance_id: $instance_id,
            agent: $agent,
            event: $event,
            cwd: $cwd,
            branch: $branch,
            updated_at: $updated_at
        }' > "$tmp"
    mv "$tmp" "$session_dir/status.json"
    printf '%s, %s\n' "$updated_at" "$event" >> "$session_dir/events.log"

    if [ "$event" = "SessionStart" ]; then
        current_commit=""
        if [ -n "$cwd" ]; then
            current_commit=$(git -C "$cwd" rev-parse HEAD 2>/dev/null || true)
        fi
        workspace_dir=$(codemate_workspace_dir "$input" 2>/dev/null || true)
        if [ -n "$workspace_dir" ]; then
            printf '%s\n' "$current_commit" > "$workspace_dir/start_commit"
        fi
    fi
}

codemate_session_is_stopped() {
    local session_dir="$1"
    jq -e '.event == "Stop"' "$session_dir/status.json" >/dev/null 2>&1
}

codemate_truthy() {
    case "${1:-}" in
        1|true|TRUE|yes|YES|on|ON) return 0 ;;
        *) return 1 ;;
    esac
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

codemate_pr_status_file() {
    local git_dir branch

    git_dir=$(git rev-parse --absolute-git-dir 2>/dev/null) || return 1
    branch=$(codemate_current_branch) || return 1
    printf '%s/codemate/pr-status/%s.json\n' "$git_dir" "$branch"
}

codemate_load_pr_reference() {
    local status_file branch state file_branch number url

    CODEMATE_CURRENT_PR_NUMBER=""
    CODEMATE_CURRENT_PR_URL=""
    CODEMATE_CURRENT_PR_STATUS_FILE=""

    if codemate_truthy "${CODEMATE_NO_PR:-}"; then
        return 1
    fi

    status_file=$(codemate_pr_status_file) || return 1
    branch=$(codemate_current_branch) || return 1
    CODEMATE_CURRENT_PR_STATUS_FILE="$status_file"

    [ -s "$status_file" ] || return 1
    state=$(jq -r '.state // "none"' "$status_file" 2>/dev/null) || return 1
    file_branch=$(jq -r '.branch // ""' "$status_file" 2>/dev/null) || return 1
    [ "$state" = "open" ] || return 1
    [ "$file_branch" = "$branch" ] || return 1
    number=$(jq -r '.number // empty' "$status_file" 2>/dev/null)
    url=$(jq -r '.url // empty' "$status_file" 2>/dev/null)

    [[ "$number" =~ ^[0-9]+$ ]] || return 1
    CODEMATE_CURRENT_PR_NUMBER="$number"
    CODEMATE_CURRENT_PR_URL="$url"
    return 0
}

codemate_write_pr_state() {
    local state="$1"
    local number="${2:-}"
    local url="${3:-}"
    local status_file branch status_dir tmp updated_at number_json

    status_file=$(codemate_pr_status_file) || return 1
    branch=$(codemate_current_branch) || return 1
    status_dir=$(dirname "$status_file")
    updated_at=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
    mkdir -p "$status_dir"
    tmp=$(mktemp "$status_dir/.pr-status.XXXXXX") || return 1

    if [[ "$number" =~ ^[0-9]+$ ]]; then
        number_json="$number"
    else
        number_json="null"
    fi

    jq -n \
        --arg state "$state" \
        --arg branch "$branch" \
        --arg url "$url" \
        --arg updated_at "$updated_at" \
        --argjson number "$number_json" \
        '{
            state: $state,
            branch: $branch,
            number: $number,
            url: $url,
            updated_at: $updated_at
        }' > "$tmp"
    mv "$tmp" "$status_file"
}
