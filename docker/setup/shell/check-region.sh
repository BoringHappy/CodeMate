#!/bin/bash
set -e

source "$(dirname "$0")/common.sh"

ALLOW_COUNTRY="${CODEMATE_ALLOW_COUNTRY:-}"
ALLOW_IP="${CODEMATE_ALLOW_IP:-}"

if [ -z "$ALLOW_COUNTRY" ] && [ -z "$ALLOW_IP" ]; then
    printf "${RED}Neither CODEMATE_ALLOW_COUNTRY nor CODEMATE_ALLOW_IP is set. Refusing to start.${RESET}\n"
    printf "${RED}Set at least one of:${RESET}\n"
    printf "${RED}  - CODEMATE_ALLOW_COUNTRY (comma-separated ip-api.com 'countryCode' values, e.g. US,CA)${RESET}\n"
    printf "${RED}  - CODEMATE_ALLOW_IP (comma-separated IPs or IPv4 CIDR ranges, e.g. 203.0.113.7,198.51.100.0/24)${RESET}\n"
    exit 1
fi

# Convert a dotted-quad IPv4 address to its 32-bit integer value.
# Echoes nothing for non-IPv4 input.
ipv4_to_int() {
    local ip="$1"
    local IFS='.'
    read -ra octets <<< "$ip"
    [ "${#octets[@]}" -eq 4 ] || return 1
    local result=0
    for octet in "${octets[@]}"; do
        case "$octet" in
            ''|*[!0-9]*) return 1 ;;
        esac
        [ "$octet" -le 255 ] || return 1
        result=$(( (result << 8) + octet ))
    done
    echo "$result"
}

# Return 0 if $1 (an IP) matches $2 (an exact IP or IPv4 CIDR like 10.0.0.0/8).
ip_matches_entry() {
    local ip="$1"
    local entry="$2"

    if [[ "$entry" == */* ]]; then
        local network="${entry%/*}"
        local bits="${entry#*/}"
        case "$bits" in
            ''|*[!0-9]*) return 1 ;;
        esac
        [ "$bits" -ge 0 ] && [ "$bits" -le 32 ] || return 1
        local ip_int net_int
        ip_int="$(ipv4_to_int "$ip")" || return 1
        net_int="$(ipv4_to_int "$network")" || return 1
        local mask=$(( bits == 0 ? 0 : (0xFFFFFFFF << (32 - bits)) & 0xFFFFFFFF ))
        [ $(( ip_int & mask )) -eq $(( net_int & mask )) ]
        return $?
    fi

    [ "$ip" = "$entry" ]
}

# Fetch a URL with a few retries (these public endpoints are occasionally flaky).
curl_retry() {
    local url="$1"
    local out=""
    for _ in 1 2 3; do
        out=$(curl -fsS --max-time 10 "$url" || true)
        if [ -n "$out" ]; then
            echo "$out"
            return 0
        fi
        sleep 1
    done
    return 1
}

printf "${CYAN}Checking access against CODEMATE_ALLOW_IP='${ALLOW_IP}' / CODEMATE_ALLOW_COUNTRY='${ALLOW_COUNTRY}'...${RESET}\n"

# CODEMATE_ALLOW_IP takes precedence: when it is set we only query ifconfig.me
# and skip the ip-api.com country lookup entirely (one fewer external call).
# The country allowlist is consulted only when CODEMATE_ALLOW_IP is unset.
ip_matched=false
country_matched=false
RESPONSE=""
CURRENT_TIMEZONE=""

if [ -n "$ALLOW_IP" ]; then
    # Detect the public IP from ifconfig.me (plain text, just the address).
    CURRENT_QUERY_IP=$(curl_retry https://ifconfig.me/ip | tr -d '[:space:]' || true)
    # Fall back to ip-api.com for the IP only if ifconfig.me was unreachable.
    if [ -z "$CURRENT_QUERY_IP" ]; then
        RESPONSE=$(curl_retry http://ip-api.com/json/ || true)
        CURRENT_QUERY_IP=$(echo "$RESPONSE" | jq -r '.query // empty' 2>/dev/null)
    fi

    if [ -z "$CURRENT_QUERY_IP" ]; then
        printf "${RED}Could not detect IP (ifconfig.me / ip-api.com).${RESET}\n"
        exit 1
    fi

    IFS=',' read -ra ALLOWED_IP_LIST <<< "$ALLOW_IP"
    for allowed in "${ALLOWED_IP_LIST[@]}"; do
        trimmed="$(echo "$allowed" | xargs)"
        [ -n "$trimmed" ] || continue
        if ip_matches_entry "$CURRENT_QUERY_IP" "$trimmed"; then
            ip_matched=true
            break
        fi
    done
else
    # No IP allowlist configured: fall back to the country allowlist via ip-api.com.
    RESPONSE=$(curl_retry http://ip-api.com/json/ || true)
    CURRENT_COUNTRY_CODE=$(echo "$RESPONSE" | jq -r '.countryCode // empty' 2>/dev/null)
    CURRENT_COUNTRY=$(echo "$RESPONSE" | jq -r '.country // empty' 2>/dev/null)
    CURRENT_REGION=$(echo "$RESPONSE" | jq -r '.region // empty' 2>/dev/null)
    CURRENT_QUERY_IP=$(echo "$RESPONSE" | jq -r '.query // empty' 2>/dev/null)

    if [ -z "$CURRENT_COUNTRY_CODE" ]; then
        printf "${RED}Could not detect country code (ip-api.com).${RESET}\n"
        printf "${RED}ip-api response: ${RESPONSE}${RESET}\n"
        exit 1
    fi

    IFS=',' read -ra ALLOWED_LIST <<< "$ALLOW_COUNTRY"
    for allowed in "${ALLOWED_LIST[@]}"; do
        trimmed="$(echo "$allowed" | xargs)"
        if [ "$trimmed" = "$CURRENT_COUNTRY_CODE" ]; then
            country_matched=true
            break
        fi
    done
fi

if [ -n "$RESPONSE" ]; then
    CURRENT_TIMEZONE=$(echo "$RESPONSE" | jq -r '.timezone // empty' 2>/dev/null)
fi

timezone_mismatched=false
if [ -n "${TZ:-}" ] && [ -n "$CURRENT_TIMEZONE" ] && [ "$CURRENT_TIMEZONE" != "$TZ" ]; then
    timezone_mismatched=true
fi

if { [ "$ip_matched" = true ] || [ "$country_matched" = true ]; } && [ "$timezone_mismatched" = false ]; then
    if [ "$ip_matched" = true ]; then
        matched_by="IP '${CURRENT_QUERY_IP}'"
    else
        matched_by="country '${CURRENT_COUNTRY_CODE}' (${CURRENT_COUNTRY})"
    fi
    printf "${GREEN}✓ Access check passed: matched by ${matched_by} (country=${CURRENT_COUNTRY_CODE}, region=${CURRENT_REGION}, ip=${CURRENT_QUERY_IP})${RESET}\n"
    exit 0
fi

# Only one allowlist is consulted per run (IP takes precedence over country),
# so report against whichever check was actually in effect.
if [ "$timezone_mismatched" = true ]; then
    printf "${RED}✗ Timezone mismatch: ip-api.com detected timezone='${CURRENT_TIMEZONE}', but TZ='${TZ}'${RESET}\n"

    ISSUE_TITLE="CodeMate timezone check failed: ${CURRENT_TIMEZONE} does not match ${TZ}"
    ISSUE_BODY=$(cat <<EOF
CodeMate refused to start because the timezone detected by ip-api.com does not
match the container's configured \`TZ\` value.

| Field | Value |
| --- | --- |
| Detected timezone (ip-api.com) | \`${CURRENT_TIMEZONE}\` |
| Configured timezone (TZ) | \`${TZ}\` |
| Detected IP (ip-api.com) | \`${CURRENT_QUERY_IP}\` |
| Branch | \`${CODEMATE_BRANCH_NAME:-unknown}\` |

ip-api.com response:

\`\`\`json
${RESPONSE}
\`\`\`
EOF
)
elif [ -n "$ALLOW_IP" ]; then
    printf "${RED}✗ Access mismatch: detected ip='${CURRENT_QUERY_IP}' is not in the IP allowlist '${ALLOW_IP}'${RESET}\n"

    ISSUE_TITLE="CodeMate access check failed: IP ${CURRENT_QUERY_IP} not allowed"
    ISSUE_BODY=$(cat <<EOF
CodeMate refused to start Claude because the container's detected IP does not
match \`CODEMATE_ALLOW_IP\`. (\`CODEMATE_ALLOW_IP\` takes precedence; the
\`CODEMATE_ALLOW_COUNTRY\` allowlist is only consulted when \`CODEMATE_ALLOW_IP\` is unset.)

| Field | Value |
| --- | --- |
| Detected IP (ifconfig.me) | \`${CURRENT_QUERY_IP}\` |
| Allowed IP(s) | \`${ALLOW_IP}\` |
| Branch | \`${CODEMATE_BRANCH_NAME:-unknown}\` |
EOF
)
else
    printf "${RED}✗ Access mismatch: detected country='${CURRENT_COUNTRY_CODE}' (${CURRENT_COUNTRY}, region=${CURRENT_REGION}, ip=${CURRENT_QUERY_IP}) is not in the country allowlist '${ALLOW_COUNTRY}'${RESET}\n"

    ISSUE_TITLE="CodeMate access check failed: country ${CURRENT_COUNTRY_CODE} not allowed"
    ISSUE_BODY=$(cat <<EOF
CodeMate refused to start Claude because the container's detected country does
not match \`CODEMATE_ALLOW_COUNTRY\`. (No \`CODEMATE_ALLOW_IP\` allowlist was configured.)

| Field | Value |
| --- | --- |
| Detected country code | \`${CURRENT_COUNTRY_CODE}\` |
| Detected country | \`${CURRENT_COUNTRY}\` |
| Detected region | \`${CURRENT_REGION}\` |
| Detected IP (ip-api.com) | \`${CURRENT_QUERY_IP}\` |
| Allowed country code(s) | \`${ALLOW_COUNTRY}\` |
| Branch | \`${CODEMATE_BRANCH_NAME:-unknown}\` |

ip-api.com response:

\`\`\`json
${RESPONSE}
\`\`\`
EOF
)
fi

if command -v gh >/dev/null 2>&1; then
    if gh issue create --title "$ISSUE_TITLE" --body "$ISSUE_BODY" 2>&1; then
        printf "${YELLOW}Filed startup-check issue on the repository.${RESET}\n"
    else
        printf "${YELLOW}Failed to file startup-check issue (gh issue create failed).${RESET}\n"
    fi
else
    printf "${YELLOW}gh CLI not available; skipping issue creation.${RESET}\n"
fi

printf "${RED}Exiting container due to startup-check mismatch.${RESET}\n"
exit 1
