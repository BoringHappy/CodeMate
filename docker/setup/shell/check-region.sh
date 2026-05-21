#!/bin/bash
set -e

source "$(dirname "$0")/common.sh"

ALLOW_REGION="${CODEMATE_ALLOW_REGION:-}"

if [ -z "$ALLOW_REGION" ]; then
    printf "${RED}CODEMATE_ALLOW_REGION is not set. Refusing to start.${RESET}\n"
    printf "${RED}Set CODEMATE_ALLOW_REGION (comma-separated list of allowed ip-api.com 'countryCode' values, e.g. US,CA) in your .env.${RESET}\n"
    exit 1
fi

printf "${CYAN}Checking country code against CODEMATE_ALLOW_REGION=${ALLOW_REGION}...${RESET}\n"

RESPONSE=$(curl -fsS --max-time 10 http://ip-api.com/json/ || true)
if [ -z "$RESPONSE" ]; then
    printf "${RED}Failed to fetch region info from http://ip-api.com/json/${RESET}\n"
    exit 1
fi

CURRENT_COUNTRY_CODE=$(echo "$RESPONSE" | jq -r '.countryCode // empty')
CURRENT_COUNTRY=$(echo "$RESPONSE" | jq -r '.country // empty')
CURRENT_REGION=$(echo "$RESPONSE" | jq -r '.region // empty')
CURRENT_QUERY_IP=$(echo "$RESPONSE" | jq -r '.query // empty')

if [ -z "$CURRENT_COUNTRY_CODE" ]; then
    printf "${RED}Could not parse 'countryCode' from ip-api.com response.${RESET}\n"
    printf "${RED}Response: ${RESPONSE}${RESET}\n"
    exit 1
fi

region_matched=false
IFS=',' read -ra ALLOWED_LIST <<< "$ALLOW_REGION"
for allowed in "${ALLOWED_LIST[@]}"; do
    trimmed="$(echo "$allowed" | xargs)"
    if [ "$trimmed" = "$CURRENT_COUNTRY_CODE" ]; then
        region_matched=true
        break
    fi
done

if [ "$region_matched" = true ]; then
    printf "${GREEN}✓ Region check passed: '${CURRENT_COUNTRY_CODE}' (${CURRENT_COUNTRY}) is allowed (region=${CURRENT_REGION}, ip=${CURRENT_QUERY_IP})${RESET}\n"
    exit 0
fi

printf "${RED}✗ Region mismatch: detected='${CURRENT_COUNTRY_CODE}' (${CURRENT_COUNTRY}, region=${CURRENT_REGION}, ip=${CURRENT_QUERY_IP}), allowed='${ALLOW_REGION}'${RESET}\n"

ISSUE_TITLE="CodeMate region check failed: ${CURRENT_COUNTRY_CODE} not in ${ALLOW_REGION}"
ISSUE_BODY=$(cat <<EOF
CodeMate refused to start Claude because the container's detected country code
does not match \`CODEMATE_ALLOW_REGION\`.

| Field | Value |
| --- | --- |
| Detected country code | \`${CURRENT_COUNTRY_CODE}\` |
| Detected country | \`${CURRENT_COUNTRY}\` |
| Detected region | \`${CURRENT_REGION}\` |
| Detected IP | \`${CURRENT_QUERY_IP}\` |
| Allowed country code(s) | \`${ALLOW_REGION}\` |
| Branch | \`${BRANCH_NAME:-unknown}\` |

ip-api.com response:

\`\`\`json
${RESPONSE}
\`\`\`
EOF
)

if command -v gh >/dev/null 2>&1; then
    if gh issue create --title "$ISSUE_TITLE" --body "$ISSUE_BODY" 2>&1; then
        printf "${YELLOW}Filed region-mismatch issue on the repository.${RESET}\n"
    else
        printf "${YELLOW}Failed to file region-mismatch issue (gh issue create failed).${RESET}\n"
    fi
else
    printf "${YELLOW}gh CLI not available; skipping issue creation.${RESET}\n"
fi

printf "${RED}Exiting container due to region mismatch.${RESET}\n"
exit 1
