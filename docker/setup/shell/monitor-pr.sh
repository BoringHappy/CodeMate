#!/bin/bash
# PR Monitor Script - Standalone script for monitoring PR comments and git changes
# Designed to be run via cron (no loop, single execution)

# Source common utilities
SCRIPT_DIR="$(dirname "$0")"
source "$SCRIPT_DIR/common.sh"

# Import environment variables from the container's init process (PID 1)
# Cron doesn't inherit Docker runtime env vars, but /proc/1/environ has them
if [ -r /proc/1/environ ]; then
    while IFS= read -r -d '' line; do
        export "$line"
    done < /proc/1/environ
fi

# Set PATH and HOME for cron environment
export PATH="/usr/local/bin:/usr/bin:/bin:/home/agent/.local/bin"
export HOME="${HOME:-/home/agent}"

# Lock file to prevent overlapping runs
LOCK_FILE="/tmp/pr-monitor.lock"
exec 200>"$LOCK_FILE"
flock -n 200 || { echo "$(date): Already running, skipping"; exit 0; }

# Configuration (can be overridden via environment variables)
CODEMATE_AGENT="${CODEMATE_AGENT:-claude}"
case "$CODEMATE_AGENT" in
    claude)
        AGENT_NAME="Claude"
        DEFAULT_AGENT_SESSION="claude-code"
        ;;
    codex)
        AGENT_NAME="Codex"
        DEFAULT_AGENT_SESSION="codex"
        ;;
    *)
        echo "Unsupported CODEMATE_AGENT: $CODEMATE_AGENT"
        exit 1
        ;;
esac
CODEMATE_AGENT_SESSION="${CODEMATE_AGENT_SESSION:-${CLAUDE_SESSION:-$DEFAULT_AGENT_SESSION}}"
CODEMATE_PR_MONITOR_STATE_FILE="${CODEMATE_PR_MONITOR_STATE_FILE:-/tmp/pr-monitor-state}"

# Derive repo directory from CODEMATE_GIT_REPO_URL (e.g., https://github.com/org/repo.git -> /home/agent/repo)
if [ -z "$CODEMATE_REPO_DIR" ] && [ -n "$CODEMATE_GIT_REPO_URL" ]; then
    REPO_NAME=$(basename "$CODEMATE_GIT_REPO_URL" .git)
    CODEMATE_REPO_DIR="/home/agent/$REPO_NAME"
fi
cd "${CODEMATE_REPO_DIR:-/home/agent/repo}" || { echo "$(date): Failed to cd to repo directory"; exit 1; }

# Function to check if a tmux session exists
session_exists() {
    tmux has-session -t "$1" 2>/dev/null
}

# Check whether the selected agent can accept another prompt. Claude exposes
# lifecycle state through its workspace hook. Codex accepts queued follow-ups,
# so an existing tmux session is sufficient.
is_agent_ready() {
    if ! session_exists "$CODEMATE_AGENT_SESSION"; then
        return 1
    fi

    if [ "$CODEMATE_AGENT" = "codex" ]; then
        return 0
    fi

    local status_file="/tmp/.session_status"
    if [ -f "$status_file" ]; then
        local last_line=$(tail -n 10 "$status_file" | grep -v '^$' | tail -n 1)
        if [[ "$last_line" =~ Stop$ ]]; then
            return 0
        fi
    fi
    return 1
}

# Claude hook state supports submission verification and retries. Codex has no
# equivalent status file here, so send its follow-up exactly once.
send_to_agent() {
    local message="$1"

    if [ "$CODEMATE_AGENT" = "codex" ]; then
        printf "%s" "$message" | tmux load-buffer -b codemate-monitor-prompt -
        tmux paste-buffer -d -b codemate-monitor-prompt -t "$CODEMATE_AGENT_SESSION"
        sleep 0.2
        tmux send-keys -t "$CODEMATE_AGENT_SESSION" Enter
        return 0
    fi

    send_and_verify_command "$CODEMATE_AGENT_SESSION" "$message" 3
}

# Load state from previous run
load_state() {
    if [ -f "$CODEMATE_PR_MONITOR_STATE_FILE" ]; then
        source "$CODEMATE_PR_MONITOR_STATE_FILE"
    else
        LAST_CHECK_TIME=""
        CONSECUTIVE_FAILURES=0
        LAST_ISSUE_COMMENT_ID=""
        READY_FOR_REVIEW_NOTIFIED="false"
        CI_FAILURE_NOTIFIED="false"
        LAST_CI_RUN_ID=""
        LAST_CI_CHECK_COMMIT=""
        NO_CI_CONFIGURED="false"
    fi
}

# Save state for next run
save_state() {
    cat > "$CODEMATE_PR_MONITOR_STATE_FILE" <<EOF
LAST_CHECK_TIME="$LAST_CHECK_TIME"
CONSECUTIVE_FAILURES=$CONSECUTIVE_FAILURES
LAST_ISSUE_COMMENT_ID="$LAST_ISSUE_COMMENT_ID"
READY_FOR_REVIEW_NOTIFIED="$READY_FOR_REVIEW_NOTIFIED"
CI_FAILURE_NOTIFIED="$CI_FAILURE_NOTIFIED"
LAST_CI_RUN_ID="$LAST_CI_RUN_ID"
LAST_CI_CHECK_COMMIT="$LAST_CI_CHECK_COMMIT"
NO_CI_CONFIGURED="$NO_CI_CONFIGURED"
EOF
}

# Cleanup and exit
cleanup_and_exit() {
    save_state
    echo "$(date): PR Monitor check completed"
    exit 0
}

# Get PR number with retry
get_pr_number() {
    local pr_number=""
    for attempt in 1 2 3; do
        pr_number=$(gh pr view --json number -q .number 2>/dev/null || echo "")
        if [ -n "$pr_number" ]; then
            echo "$pr_number"
            return 0
        fi
        sleep 2
    done
    return 1
}

# Check for unsolved PR comments and return both count and details
check_pr_comments() {
    local pr_number="$1"
    local time_filter=""

    if [ -n "$LAST_CHECK_TIME" ]; then
        time_filter="| map(select(.created_at > \"$LAST_CHECK_TIME\"))"
    fi

    local comments_data=""
    for attempt in 1 2 3; do
        comments_data=$(gh api repos/:owner/:repo/pulls/"$pr_number"/comments --jq "
            . $time_filter |
            map(select(.user.login | endswith(\"[bot]\") | not)) |
            group_by(.in_reply_to_id // .id) |
            map(
                if (.[-1].body | startswith(\"CodeMate Replied:\")) then
                    empty
                else
                    .[-1]
                end
            )
        " 2>/dev/null)

        if [ $? -eq 0 ] && [ -n "$comments_data" ]; then
            CONSECUTIVE_FAILURES=0
            echo "$comments_data"
            return 0
        fi

        sleep $((attempt * 2))
    done

    CONSECUTIVE_FAILURES=$((CONSECUTIVE_FAILURES + 1))
    return 1
}

# Check for new Issue Comments (pure PR comments) and send content to the agent
check_issue_comments() {
    local pr_number="$1"

    # Fetch issue comments with reactions in a single API call
    # Filter out comments that: are already processed, start with an agent reply marker, or have eyes reaction
    local comments=""
    for attempt in 1 2 3; do
        if [ -n "$LAST_ISSUE_COMMENT_ID" ]; then
            comments=$(gh api repos/:owner/:repo/issues/"$pr_number"/comments --jq "
                map(select(.id > $LAST_ISSUE_COMMENT_ID)) |
                map(select(.user.login | endswith(\"[bot]\") | not)) |
                map(select(.body | startswith(\"CodeMate Replied:\") | not)) |
                map(select(.reactions.eyes == 0)) |
                sort_by(.id)
            " 2>/dev/null)
        else
            comments=$(gh api repos/:owner/:repo/issues/"$pr_number"/comments --jq "
                map(select(.user.login | endswith(\"[bot]\") | not)) |
                map(select(.body | startswith(\"CodeMate Replied:\") | not)) |
                map(select(.reactions.eyes == 0)) |
                sort_by(.id)
            " 2>/dev/null)
        fi

        if [ $? -eq 0 ]; then
            break
        fi
        sleep $((attempt * 2))
    done

    if [ -z "$comments" ] || [ "$comments" = "[]" ]; then
        return 0
    fi

    # Process the first unacknowledged comment
    local comment_count=$(echo "$comments" | jq 'length')
    if [ "$comment_count" -gt 0 ]; then
        local comment_id=$(echo "$comments" | jq -r '.[0].id')
        local comment_body=$(echo "$comments" | jq -r '.[0].body')
        local comment_user=$(echo "$comments" | jq -r '.[0].user.login')

        echo "$(date): Processing issue comment #$comment_id from $comment_user"

        if session_exists "$CODEMATE_AGENT_SESSION"; then
            if [ "$CODEMATE_AGENT" = "claude" ]; then
                ack_instruction="use /pr:ack-comments skill"
            else
                ack_instruction="use the pr plugin's ack-comments skill"
            fi
            send_to_agent "PR Comment from $comment_user: $comment_body (After addressing, $ack_instruction to add 👀 reaction)"
            LAST_ISSUE_COMMENT_ID="$comment_id"
            return 1  # Signal that we sent a comment
        fi
    fi

    return 0
}

# Check if PR is ready for review (not draft) and doesn't have pr-updated label
check_pr_ready_for_review() {
    local pr_number="$1"

    # Skip if we've already notified
    if [ "$READY_FOR_REVIEW_NOTIFIED" = "true" ]; then
        return 0
    fi

    # Fetch PR status and labels in a single API call
    local pr_data=""
    for attempt in 1 2 3; do
        pr_data=$(gh pr view "$pr_number" --json isDraft,labels 2>/dev/null)
        if [ $? -eq 0 ] && [ -n "$pr_data" ]; then
            break
        fi
        sleep $((attempt * 2))
    done

    if [ -z "$pr_data" ]; then
        return 0
    fi

    # Check if PR is not draft (ready for review)
    local is_draft=$(echo "$pr_data" | jq -r '.isDraft')
    if [ "$is_draft" = "true" ]; then
        return 0
    fi

    # Check if PR already has pr-updated label
    local has_label=$(echo "$pr_data" | jq -r '.labels[] | select(.name == "pr-updated") | .name')
    if [ -n "$has_label" ]; then
        echo "$(date): PR already has pr-updated label, skipping notification"
        READY_FOR_REVIEW_NOTIFIED="true"
        return 0
    fi

    # PR is ready for review and doesn't have the label
    echo "$(date): PR is ready for review, notifying $AGENT_NAME"
    if session_exists "$CODEMATE_AGENT_SESSION"; then
        if [ "$CODEMATE_AGENT" = "claude" ]; then
            update_instruction="use /pr:update skill"
        else
            update_instruction="use the pr plugin's update skill"
        fi
        send_to_agent "The PR is now ready for review. Please $update_instruction to update the PR title and description based on all changes made."
        READY_FOR_REVIEW_NOTIFIED="true"
        return 1  # Signal that we sent a notification
    fi

    return 0
}

# Check for CI failures and notify the selected agent
check_ci_status() {
    local pr_number="$1"

    # Skip if no CI is configured for this repo
    if [ "$NO_CI_CONFIGURED" = "true" ]; then
        return 0
    fi

    # Get current HEAD commit
    local current_commit=$(git rev-parse HEAD 2>/dev/null)

    # Skip if no new code has been pushed since last check
    if [ "$current_commit" = "$LAST_CI_CHECK_COMMIT" ]; then
        return 0
    fi

    # Skip if we've already notified about current failure
    if [ "$CI_FAILURE_NOTIFIED" = "true" ]; then
        # Check if there's a new run (CI was re-triggered)
        local latest_run_id=$(gh run list --branch "$(git branch --show-current)" --limit 1 --json databaseId -q '.[0].databaseId' 2>/dev/null)
        if [ "$latest_run_id" = "$LAST_CI_RUN_ID" ]; then
            return 0
        fi
        # New run detected, reset notification flag
        CI_FAILURE_NOTIFIED="false"
    fi

    # Get check status for the PR
    # First, check if any CI checks are configured using statusCheckRollup
    local check_count=""
    for attempt in 1 2 3; do
        check_count=$(gh pr view "$pr_number" --json statusCheckRollup -q '.statusCheckRollup | length' 2>/dev/null)
        if [ $? -eq 0 ]; then
            break
        fi
        sleep $((attempt * 2))
    done

    if [ "$check_count" = "0" ] || [ -z "$check_count" ]; then
        # No CI checks configured for this PR, skip future checks
        echo "$(date): No CI checks configured for this PR, skipping future CI checks"
        NO_CI_CONFIGURED="true"
        return 0
    fi

    # Get detailed check status
    local checks_output=""
    for attempt in 1 2 3; do
        checks_output=$(gh pr checks "$pr_number" 2>/dev/null)
        if [ $? -eq 0 ]; then
            break
        fi
        sleep $((attempt * 2))
    done

    if [ -z "$checks_output" ]; then
        return 0
    fi

    # Check if CI is still running (don't update state while pending)
    local pending_checks=$(echo "$checks_output" | grep -iE "pending|running|queued" || true)
    if [ -n "$pending_checks" ]; then
        return 0
    fi

    # Check for failures (look for "fail" in output)
    local failed_checks=$(echo "$checks_output" | grep -i "fail" || true)

    if [ -z "$failed_checks" ]; then
        # No failures and CI completed, update last checked commit
        CI_FAILURE_NOTIFIED="false"
        LAST_CI_CHECK_COMMIT="$current_commit"
        return 0
    fi

    echo "$(date): CI failures detected"

    # Get the failed workflow run details
    local failed_run=$(gh run list --branch "$(git branch --show-current)" --status failure --limit 1 --json databaseId,name,conclusion -q '.[0]' 2>/dev/null)

    if [ -z "$failed_run" ] || [ "$failed_run" = "null" ]; then
        return 0
    fi

    local run_id=$(echo "$failed_run" | jq -r '.databaseId')
    local run_name=$(echo "$failed_run" | jq -r '.name')

    # Store run ID to track if it changes
    LAST_CI_RUN_ID="$run_id"

    # Get failed jobs from the run
    local failed_jobs=$(gh run view "$run_id" --json jobs -q '.jobs[] | select(.conclusion == "failure") | .name' 2>/dev/null)

    # Get logs for the failed run (last 100 lines of failed job)
    local failure_logs=""
    failure_logs=$(gh run view "$run_id" --log-failed 2>/dev/null | tail -100)

    if session_exists "$CODEMATE_AGENT_SESSION"; then
        if [ "$CODEMATE_AGENT" = "claude" ]; then
            commit_instruction="using /git:commit skill"
        else
            commit_instruction="using the git plugin's commit skill"
        fi
        local message="CI check failed for this PR. Please analyze and fix the issue.

Workflow: $run_name
Failed jobs: $failed_jobs

Recent failure logs:
\`\`\`
$failure_logs
\`\`\`

Please fix the CI failure and commit the changes $commit_instruction."

        send_to_agent "$message"
        CI_FAILURE_NOTIFIED="true"
        LAST_CI_CHECK_COMMIT="$current_commit"
        return 1  # Signal that we sent a notification
    fi

    return 0
}

# Main logic (single run)
main() {
    echo "$(date): PR Monitor check started"

    load_state

    # Check whether the selected agent can accept a follow-up.
    if ! is_agent_ready; then
        echo "$(date): $AGENT_NAME session is not ready, skipping"
        cleanup_and_exit
    fi

    # Get PR number
    pr_number=$(get_pr_number) || {
        echo "$(date): No PR found"
        cleanup_and_exit
    }

    echo "$(date): Checking PR #$pr_number"

    # Check for CI failures
    check_ci_status "$pr_number"
    ci_failure_sent=$?

    # If we sent a CI failure notification, skip other checks this run
    if [ "$ci_failure_sent" -eq 1 ]; then
        echo "$(date): CI failure notification sent to $AGENT_NAME, skipping other checks"
        cleanup_and_exit
    fi

    # Check if PR is ready for review (not draft) and needs update
    check_pr_ready_for_review "$pr_number"
    ready_for_review_sent=$?

    # If we sent a ready-for-review notification, skip other checks this run
    if [ "$ready_for_review_sent" -eq 1 ]; then
        echo "$(date): Ready-for-review notification sent to $AGENT_NAME, skipping other checks"
        cleanup_and_exit
    fi

    # Skip API call if too many failures
    if [ "$CONSECUTIVE_FAILURES" -ge 5 ]; then
        echo "$(date): Too many failures, skipping API call"
        cleanup_and_exit
    fi

    # Check for new Issue Comments (pure PR comments)
    check_issue_comments "$pr_number"
    issue_comment_sent=$?

    # If we sent an issue comment, skip review comments check this run
    if [ "$issue_comment_sent" -eq 1 ]; then
        echo "$(date): Issue comment sent to $AGENT_NAME, skipping review comments check"
        cleanup_and_exit
    fi

    comments_data=$(check_pr_comments "$pr_number") || {
        echo "$(date): API call failed"
        cleanup_and_exit
    }

    unsolved_count=$(echo "$comments_data" | jq 'length')
    echo "$(date): unsolved_count=$unsolved_count"

    if [ "$unsolved_count" -gt 0 ]; then
        echo "$(date): Unsolved PR comments detected ($unsolved_count)"
        if session_exists "$CODEMATE_AGENT_SESSION"; then
            # Format comment details for the selected agent.
            comment_summary=$(echo "$comments_data" | jq -r '
                map(
                    "- \(.path):\(.line // .original_line) by @\(.user.login):\n  \(.body | split("\n") | join("\n  "))"
                ) | join("\n\n")
            ')

            if [ "$CODEMATE_AGENT" = "claude" ]; then
                fix_instruction="use /pr:fix-comments skill"
            else
                fix_instruction="use the pr plugin's fix-comments skill"
            fi
            local message="Please $fix_instruction to address the following PR review comments:

$comment_summary"
            send_to_agent "$message"
        fi
        LAST_CHECK_TIME=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
    fi

    save_state
    echo "$(date): PR Monitor check completed"
}

main "$@"
