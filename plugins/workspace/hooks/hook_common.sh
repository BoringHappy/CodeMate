#!/usr/bin/env bash

# Shared state helpers for workspace lifecycle hooks. Runtime state is scoped by
# the agent-provided session ID, while monitor cursors are scoped by Git worktree
# and branch. PR state itself lives in GitHub and is resolved through the pr
# plugin's `pr-status` interface; the workspace plugin never parses a shared
# PR-status file. This keeps concurrent repositories, worktrees, and agent
# sessions from reading or overwriting one another's coordination files.

codemate_runtime_root() {
    local root

    if [ -n "${CODEMATE_RUNTIME_DIR:-}" ]; then
        root="$CODEMATE_RUNTIME_DIR"
    elif [ -n "${CODEMATE_TMPDIR:-}" ]; then
        # CodeMate-scoped temp root (per agent), never the global TMPDIR that
        # every process in the container inherits.
        root="$CODEMATE_TMPDIR/codemate"
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

codemate_event_fingerprint() {
    local input="$1"

    printf '%s' "$input" | jq -cS '{
        session_id,
        turn_id: (.turn_id // null),
        hook_event_name,
        cwd,
        stop_hook_active: (.stop_hook_active // null),
        last_assistant_message: (.last_assistant_message // null),
        prompt: (.prompt // null)
    }' 2>/dev/null | sha256sum | awk '{print $1}'
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
    local session_dir session_id event event_fingerprint cwd branch updated_at tmp current_commit workspace_dir

    session_dir=$(codemate_session_dir "$input") || return 1
    session_id=$(codemate_session_id "$input") || return 1
    event=$(printf '%s' "$input" | jq -r '.hook_event_name // "Unknown"')
    event_fingerprint=$(codemate_event_fingerprint "$input") || return 1
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
        --arg event_fingerprint "$event_fingerprint" \
        --arg cwd "$cwd" \
        --arg branch "$branch" \
        --arg updated_at "$updated_at" \
        '{
            session_id: $session_id,
            instance_id: $instance_id,
            agent: $agent,
            event: $event,
            event_fingerprint: $event_fingerprint,
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
    local expected_fingerprint="${2:-}"

    if [ -n "$expected_fingerprint" ]; then
        jq -e --arg fingerprint "$expected_fingerprint" \
            '.event == "Stop" and .event_fingerprint == $fingerprint' \
            "$session_dir/status.json" >/dev/null 2>&1
    else
        jq -e '.event == "Stop"' "$session_dir/status.json" >/dev/null 2>&1
    fi
}

codemate_prompt_history_file() {
    # Codex and Claude both record every user prompt in a history.jsonl:
    # Codex uses $CODEX_HOME/history.jsonl and Claude uses
    # $CLAUDE_CONFIG_DIR/history.jsonl. A new entry is appended as soon as the
    # user submits a message, so Stop hooks can notice a pending prompt even
    # while the agent session is still blocked finishing the previous turn.
    local agent codex_file claude_file
    codex_file="${CODEX_HOME:-${HOME:-}/.codex}/history.jsonl"
    claude_file="${CLAUDE_CONFIG_DIR:-${HOME:-}/.claude}/history.jsonl"

    agent=$(codemate_agent_name)
    case "$agent" in
        codex)
            [ -f "$codex_file" ] && { printf '%s\n' "$codex_file"; return 0; }
            ;;
        claude)
            [ -f "$claude_file" ] && { printf '%s\n' "$claude_file"; return 0; }
            ;;
    esac
    return 1
}

# Returns 0 when the hook can tell which runtime it belongs to. CodeMate
# containers export CODEMATE_AGENT; Codex hooks additionally set PLUGIN_ROOT
# and Claude hooks set CLAUDE_PLUGIN_ROOT. Plain CLI sessions may set none of
# them, in which case both histories are consulted below.
codemate_runtime_is_identified() {
    [ -n "${CODEMATE_AGENT:-}" ] && return 0
    [ -n "${PLUGIN_ROOT:-}" ] && return 0
    [ -n "${CLAUDE_PLUGIN_ROOT:-}" ] && return 0
    return 1
}

# Prints the newest user-prompt timestamp recorded for a session, or 0 when
# the agent keeps no readable prompt history. Codex timestamps are epoch
# seconds; Claude uses epoch milliseconds. When the runtime cannot be
# identified, both histories are checked so a machine with Codex and Claude
# installed side by side never mistakes one runtime's prompts for the other's.
codemate_latest_prompt_ts() {
    local session_id="$1" history_file latest ts codex_file claude_file
    latest=""

    if codemate_runtime_is_identified; then
        history_file=$(codemate_prompt_history_file) || { printf '0\n'; return 0; }
        if codemate_is_codex; then
            latest=$(jq -r --arg sid "$session_id" 'select(.session_id == $sid) | .ts' "$history_file" 2>/dev/null | tail -1) || true
        else
            latest=$(jq -r --arg sid "$session_id" 'select(.sessionId == $sid) | .timestamp' "$history_file" 2>/dev/null | tail -1) || true
        fi
    else
        codex_file="${CODEX_HOME:-${HOME:-}/.codex}/history.jsonl"
        claude_file="${CLAUDE_CONFIG_DIR:-${HOME:-}/.claude}/history.jsonl"
        if [ -f "$codex_file" ]; then
            ts=$(jq -r --arg sid "$session_id" 'select(.session_id == $sid) | .ts' "$codex_file" 2>/dev/null | tail -1) || true
            [ -n "$ts" ] && [ "$ts" -gt "${latest:-0}" ] 2>/dev/null && latest="$ts"
        fi
        if [ -f "$claude_file" ]; then
            ts=$(jq -r --arg sid "$session_id" 'select(.sessionId == $sid) | .timestamp' "$claude_file" 2>/dev/null | tail -1) || true
            [ -n "$ts" ] && [ "$ts" -gt "${latest:-0}" ] 2>/dev/null && latest="$ts"
        fi
    fi

    if [ -n "$latest" ] && [ "$latest" -ge 0 ] 2>/dev/null; then
        printf '%s\n' "$latest"
    else
        printf '0\n'
    fi
    return 0
}

# Returns 0 when a user prompt newer than the baseline has been recorded for
# the session, meaning the user is waiting and Stop hooks should stop polling.
codemate_has_new_prompt() {
    local session_id="$1" baseline_ts="$2" current_ts
    current_ts=$(codemate_latest_prompt_ts "$session_id") || current_ts=0
    [ "$current_ts" != "0" ] && [ "$current_ts" -gt "$baseline_ts" ] 2>/dev/null
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

codemate_worktree_key() {
    local git_dir branch

    git_dir=$(git rev-parse --absolute-git-dir 2>/dev/null) || return 1
    branch=$(codemate_current_branch) || return 1
    printf '%s\n%s' "$git_dir" "$branch" | sha256sum | awk '{print $1}'
}

# Workspace-private monitor cursor/lock base, keyed by Git worktree + branch.
# This is runtime state, so it lives under the runtime root (never inside .git).
codemate_monitor_state_base() {
    local key root

    key=$(codemate_worktree_key) || return 1
    root=$(codemate_runtime_root) || return 1
    mkdir -p "$root/monitor"
    printf '%s/monitor/%s\n' "$root" "$key"
}

# Locates the pr plugin's `pr-status.sh` interface. Resolution order:
#   1. CODEMATE_PR_PLUGIN_ROOT (explicit install-time override)
#   2. Same-marketplace sibling of this plugin (env roots or hook directory)
#   3. Plugin CLI discovery (plain Claude Code / Codex installs)
codemate_pr_status_script() {
    local candidate base hook_dir

    if [ -n "${CODEMATE_PR_PLUGIN_ROOT:-}" ]; then
        candidate="$CODEMATE_PR_PLUGIN_ROOT/scripts/pr-status.sh"
        [ -x "$candidate" ] && { printf '%s\n' "$candidate"; return 0; }
    fi

    for base in \
        "${CODEMATE_PLUGIN_ROOT:-}/../pr" \
        "${PLUGIN_ROOT:-}/../pr" \
        "${CLAUDE_PLUGIN_ROOT:-}/../pr"; do
        [ -n "$base" ] || continue
        candidate="$base/scripts/pr-status.sh"
        [ -x "$candidate" ] && { printf '%s\n' "$candidate"; return 0; }
        # Versioned cache layout, e.g. ~/.claude/plugins/cache/codemate/pr/<ver>.
        for candidate in "$base"/*/scripts/pr-status.sh; do
            [ -x "$candidate" ] && { printf '%s\n' "$candidate"; return 0; }
        done
    done

    # Relative to this hook's own directory (repo checkout or marketplace cache).
    hook_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd 2>/dev/null) || return 1
    candidate="$hook_dir/../../pr/scripts/pr-status.sh"
    [ -x "$candidate" ] && { printf '%s\n' "$candidate"; return 0; }
    for candidate in "$hook_dir"/../../pr/*/scripts/pr-status.sh; do
        [ -x "$candidate" ] && { printf '%s\n' "$candidate"; return 0; }
    done

    if command -v codex >/dev/null 2>&1; then
        candidate=$(codex plugin list --json 2>/dev/null | jq -r '.installed[] | select(.name == "pr") | .source.path' | head -1)
        if [ -n "$candidate" ]; then
            candidate="$candidate/scripts/pr-status.sh"
            [ -x "$candidate" ] && { printf '%s\n' "$candidate"; return 0; }
        fi
    fi
    if command -v claude >/dev/null 2>&1; then
        candidate=$(claude plugin list --json 2>/dev/null | jq -r '.[] | select((.id | split("@")[0]) == "pr") | .installPath' | head -1)
        if [ -n "$candidate" ]; then
            candidate="$candidate/scripts/pr-status.sh"
            [ -x "$candidate" ] && { printf '%s\n' "$candidate"; return 0; }
        fi
    fi
    return 1
}

# Resolves the open PR for the current worktree/branch. GitHub is the source of
# truth; the pr plugin's `pr-status get` is the canonical implementation, with
# an inline fallback (same contract) when the plugin is not installed.
codemate_load_pr_reference() {
    local script output number url branch owner upstream

    CODEMATE_CURRENT_PR_NUMBER=""
    CODEMATE_CURRENT_PR_URL=""

    if codemate_truthy "${CODEMATE_NO_PR:-}"; then
        return 1
    fi

    script=$(codemate_pr_status_script 2>/dev/null) || script=""
    if [ -n "$script" ]; then
        # Preferred path: the pr plugin owns PR resolution.
        output=$("$script" get 2>/dev/null) || return 1
        number=$(printf '%s' "$output" | jq -r '.number // empty' 2>/dev/null)
        url=$(printf '%s' "$output" | jq -r '.url // empty' 2>/dev/null)
    else
        # Fallback: same query-first contract, inlined.
        branch=$(codemate_current_branch) || return 1
        if git remote get-url upstream >/dev/null 2>&1; then
            upstream=$(git remote get-url upstream | sed 's/.*github.com[:/]//' | sed 's/.git$//')
            owner=$(git remote get-url origin | sed 's/.*github.com[:/]//' | sed 's/.git$//' | cut -d'/' -f1)
            output=$(gh pr list --repo "$upstream" --head "$owner:$branch" --state open --json number,url,state -q '.[0]' 2>/dev/null) || return 1
        else
            output=$(gh pr list --head "$branch" --state open --json number,url,state -q '.[0]' 2>/dev/null) || return 1
        fi
        [ -n "$output" ] && [ "$output" != "null" ] || return 1
        number=$(printf '%s' "$output" | jq -r '.number // empty' 2>/dev/null)
        url=$(printf '%s' "$output" | jq -r '.url // empty' 2>/dev/null)
    fi

    [[ "$number" =~ ^[0-9]+$ ]] || return 1
    CODEMATE_CURRENT_PR_NUMBER="$number"
    CODEMATE_CURRENT_PR_URL="$url"
    return 0
}

# Shared contract values between the pr plugin and the workspace monitor. All
# are overridable via environment so deployments can rebrand or reuse these
# plugins without changing code. See docs/plugin-contracts.md.
codemate_reply_prefix() {
    printf '%s\n' "${CODEMATE_REPLY_PREFIX:-CodeMate Replied:}"
}

codemate_ack_reaction() {
    printf '%s\n' "${CODEMATE_ACK_REACTION:-eyes}"
}

codemate_ack_emoji() {
    case "$(codemate_ack_reaction)" in
        eyes) printf '👀' ;;
        rocket) printf '🚀' ;;
        heart) printf '❤️' ;;
        hooray) printf '🎉' ;;
        laugh) printf '😄' ;;
        "+1") printf '👍' ;;
        "-1") printf '👎' ;;
        confused) printf '😕' ;;
        *) printf '👀' ;;
    esac
}

codemate_pr_updated_label() {
    printf '%s\n' "${CODEMATE_PR_UPDATED_LABEL:-pr-updated}"
}

codemate_commit_command() {
    printf '%s\n' "${CODEMATE_COMMIT_COMMAND:-git:commit}"
}

codemate_fix_comments_command() {
    printf '%s\n' "${CODEMATE_FIX_COMMENTS_COMMAND:-pr:fix-comments}"
}

codemate_ack_comments_command() {
    printf '%s\n' "${CODEMATE_ACK_COMMENTS_COMMAND:-pr:ack-comments}"
}

codemate_update_command() {
    printf '%s\n' "${CODEMATE_UPDATE_COMMAND:-pr:update}"
}

# Renders an agent-appropriate reference to a skill: Codex uses
# "the <name> skill", Claude Code uses "/<name>".
codemate_skill_phrase() {
    local command_name="$1"

    if codemate_is_codex; then
        printf 'the %s skill' "$command_name"
    else
        printf '/%s' "$command_name"
    fi
}
