#!/bin/bash
# Common shell utilities for setup scripts

# Color codes
export CYAN='\033[1;36m'
export GREEN='\033[1;32m'
export YELLOW='\033[1;33m'
export RED='\033[1;31m'
export BLUE='\033[1;34m'
export RESET='\033[0m'

# Function to run a setup script with formatted output
# Usage: run_setup_script "script-name.sh" "Description"
run_setup_script() {
    local script_name="$1"
    local description="${2:-Running $script_name...}"

    printf "\n${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}\n"
    printf "${CYAN}${description}${RESET}\n"
    printf "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}\n"

    # Determine how to run the script based on extension
    if [[ "$script_name" == *.py ]]; then
        python3 "$script_name"
    else
        bash "$script_name"
    fi
}

# Function to add a Claude plugin marketplace (checks if already added)
# Usage: add_marketplace "index/total" "marketplace-name" "marketplace-path"
add_marketplace() {
    local progress="$1"
    local name="$2"
    local path="$3"

    # Check if marketplace is already added
    local existing_marketplaces=$(claude plugin marketplace list 2>/dev/null || echo "")
    if echo "$existing_marketplaces" | grep -q "$path"; then
        printf "  [${progress}] ${GREEN}✓ ${name} marketplace already added${RESET}\n"
        return 0
    fi

    printf "  [${progress}] Adding ${name} marketplace...\n"
    if claude plugin marketplace add "$path" 2>&1; then
        printf "  ${GREEN}✓ ${name} marketplace added${RESET}\n"
        return 0
    else
        printf "  ${YELLOW}⚠ Failed to add ${name} marketplace${RESET}\n"
        return 1
    fi
}

# Function to update all Claude plugin marketplaces
# Usage: update_marketplaces
update_marketplaces() {
    printf "  Updating marketplaces to fetch latest plugin information...\\n"
    if claude plugin marketplace update 2>&1; then
        printf "  ${GREEN}✓ Marketplaces updated successfully${RESET}\\n"
        return 0
    else
        printf "  ${YELLOW}⚠ Failed to update marketplaces${RESET}\\n"
        return 1
    fi
}

# Function to install (if needed), verify, and update a Claude plugin
# Usage: install_and_update_plugin "index/total" "plugin-name" "skill1, skill2, skill3"
install_and_update_plugin() {
    local progress="$1"
    local plugin="$2"
    local skills="$3"

    # Check if plugin is already installed
    local installed_list=$(claude plugin list 2>/dev/null || echo "")
    if echo "$installed_list" | grep -q "$plugin"; then
        printf "  [${progress}] ${GREEN}✓ ${plugin} already installed${RESET}\n"
        if [ -n "$skills" ]; then
            printf "    Skills: ${skills}\n"
        fi
    else
        printf "  [${progress}] Installing ${plugin}...\n"
        if claude plugin install "$plugin" 2>&1; then
            printf "  ${GREEN}✓ ${plugin} installed${RESET}\n"

            # Verify installation
            installed_list=$(claude plugin list 2>/dev/null || echo "")
            if echo "$installed_list" | grep -q "$plugin"; then
                if [ -n "$skills" ]; then
                    printf "    Skills: ${skills}\n"
                fi
            else
                printf "  ${YELLOW}⚠ Plugin installed but not found in list${RESET}\n"
                return 1
            fi
        else
            printf "  ${RED}✗ ${plugin} installation failed${RESET}\n"
            return 1
        fi
    fi

    printf "  [${progress}] Updating ${plugin}...\n"
    if claude plugin update "$plugin" 2>&1; then
        printf "  ${GREEN}✓ ${plugin} updated${RESET}\n"
        return 0
    else
        printf "  ${YELLOW}⚠ Failed to update ${plugin}${RESET}\n"
        return 1
    fi
}

# Function to send command to tmux session and verify submission with retry
# Usage: send_and_verify_command <session_name> <command> <max_attempts>
send_and_verify_command() {
    local session_name="$1"
    local command="$2"
    local max_attempts="${3:-3}"
    local attempt=1
    local SESSION_STATUS_FILE="/tmp/.session_status"

    # Send the command
    # Use literal "Enter" key name rather than C-m: with extended-keys always
    # enabled in tmux.conf, C-m is encoded as a CSI u modified-key sequence
    # (\x1b[109;5u) that Claude Code does not interpret as Enter, so the
    # prompt would never submit.
    tmux send-keys -t "$session_name" "$command"
    tmux send-keys -t "$session_name" Enter

    # Verify submission with retry
    while [ $attempt -le $max_attempts ]; do
        sleep 3

        if [ -f "$SESSION_STATUS_FILE" ]; then
            STATUS=$(cat "$SESSION_STATUS_FILE" 2>/dev/null || echo "")
            if [ "$STATUS" = "UserPromptSubmit" ]; then
                printf "${GREEN}Command submitted successfully (attempt $attempt)${RESET}\n"
                return 0
            else
                printf "${YELLOW}Command not submitted (status: $STATUS), attempt $attempt/$max_attempts${RESET}\n"
                if [ $attempt -lt $max_attempts ]; then
                    tmux send-keys -t "$session_name" Enter
                fi
            fi
        else
            printf "${YELLOW}Session status file not found on attempt $attempt${RESET}\n"
        fi

        attempt=$((attempt + 1))
    done

    printf "${YELLOW}Max retry attempts reached, continuing anyway${RESET}\n"
    return 0
}

# Standardized logging helpers (reuse the color codes defined above).
# Each prints a leveled, colorized message to stderr so it never pollutes
# values captured via command substitution.
# Usage: log_info "message" / log_warn "message" / log_error "message"
log_info() {
    printf "${GREEN}[INFO]${RESET} %s\n" "$*" >&2
}

log_warn() {
    printf "${YELLOW}[WARN]${RESET} %s\n" "$*" >&2
}

log_error() {
    printf "${RED}[ERROR]${RESET} %s\n" "$*" >&2
}


