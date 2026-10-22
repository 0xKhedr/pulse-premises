#!/usr/bin/env bash
# First installation only. Run from a checkout with generated .env.building.
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ $# -ne 1 ]]; then
    echo "Usage: bash linux/initialize-building.sh <stack-admin-email>" >&2
    exit 1
fi
compose=(docker compose --env-file .env.building -f docker-compose.building.yml)
"${compose[@]}" config --quiet
"${compose[@]}" up -d --wait postgres redis
"${compose[@]}" run --rm --no-deps --user 0 --entrypoint /bin/sh stack -ec \
  'chown 886:886 /srv/ttn-lorawan/public/blob'
"${compose[@]}" run --rm --no-deps stack is-db migrate
# The CLI prompts for a password; it is not placed in shell history or logs.
admin_args=(is-db create-admin-user --id admin --email "$1")
if [[ -n "${TTS_BOOTSTRAP_PASSWORD:-}" ]]; then
    # Non-interactive installations and CI may supply this through the process
    # environment. Never use `set -x` with credentials in the environment.
    admin_args+=(--password "$TTS_BOOTSTRAP_PASSWORD")
fi
"${compose[@]}" run --rm --no-deps stack "${admin_args[@]}"
# The console secret is read from the container environment. It is never
# sourced into the host shell or printed by this script.
"${compose[@]}" run --rm --no-deps --entrypoint /bin/sh stack -ec '
    exec ttn-lw-stack -c /config/ttn-lw-stack.yml is-db create-oauth-client \
      --id console --name Console --owner admin \
      --secret "$TTN_LW_CONSOLE_OAUTH_CLIENT_SECRET" \
      --redirect-uri "$TTN_LW_CONSOLE_UI_CANONICAL_URL/oauth/callback" \
      --redirect-uri /console/oauth/callback \
      --logout-redirect-uri "$TTN_LW_CONSOLE_UI_CANONICAL_URL" \
      --logout-redirect-uri /console
'
"${compose[@]}" up -d --wait stack
echo "The Things Stack is initialized. Configure the application, API key and webhook in deploy/README.md."
