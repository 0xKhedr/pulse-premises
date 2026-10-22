#!/usr/bin/env bash
# Register the missing Console OAuth client for an existing TTN user.
# Uses the current configuration; does not change settings or restart services.
set -euo pipefail

usage() {
    echo "Usage: bash linux/create-console-client.sh [ttn-user-id]"
    echo "Omit the user ID to enter it when prompted. Use the TTN user ID, not an email."
}

if [[ $# -gt 1 ]]; then
    usage >&2
    exit 2
fi
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    usage
    exit 0
fi

ttn_owner="${1:-}"
if [[ -z "$ttn_owner" ]]; then
    if ! read -r -p "Existing TTN user ID: " ttn_owner; then
        echo "Pass your TTN user ID as the script argument." >&2
        exit 2
    fi
fi
if [[ -z "${ttn_owner//[[:space:]]/}" ]]; then
    echo "A TTN user ID is required." >&2
    exit 2
fi

cd "$(dirname "$0")/.."
if [[ ! -f .env.building || ! -f deploy/stack/ttn-lw-stack.yml ]]; then
    echo "Existing .env.building and deploy/stack/ttn-lw-stack.yml files are required." >&2
    exit 1
fi

compose=(docker compose --env-file .env.building -f docker-compose.building.yml)
"${compose[@]}" config --quiet
# Read the secret inside the container. Warning-level logging suppresses the
# CLI's success message, which otherwise includes the client secret.
"${compose[@]}" run --rm --no-deps --entrypoint /bin/sh stack -ec '
    exec ttn-lw-stack -c /config/ttn-lw-stack.yml --log.level warn is-db create-oauth-client \
      --id console --name Console --owner "$1" \
      --secret "$TTN_LW_CONSOLE_OAUTH_CLIENT_SECRET" \
      --redirect-uri "$TTN_LW_CONSOLE_UI_CANONICAL_URL/oauth/callback" \
      --redirect-uri /console/oauth/callback \
      --logout-redirect-uri "$TTN_LW_CONSOLE_UI_CANONICAL_URL" \
      --logout-redirect-uri /console
' sh "$ttn_owner"

printf 'Console OAuth client created for TTN user %s.\n' "$ttn_owner"
