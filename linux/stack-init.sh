#!/bin/sh
# Runs inside the stack container before the server starts.
set -eu
umask 077

is_db() {
    # Suppress success output, which can include the Console client secret.
    ttn-lw-stack -c /config/ttn-lw-stack.yml --log.level warn is-db "$@"
}

ensure_resource() {
    resource="$1"
    marker_file="$state_dir/$2"
    duplicate_code="$3"
    shift 3
    if [ -f "$marker_file" ]; then
        printf '%s already initialized; preserving it.\n' "$resource"
        return 0
    fi
    if is_db "$@" > "$result_file" 2>&1; then
        printf 'Created %s.\n' "$resource"
    else
        status=$?
        # Only an explicit duplicate-resource error is recoverable.
        # Authentication, connection, validation, and migration errors must stop setup.
        if grep -Eq "error:[^[:space:]]*:(already_exists|$duplicate_code)([[:space:](]|$)" "$result_file"; then
            printf '%s already exists; preserving it.\n' "$resource"
        else
            cat "$result_file" >&2
            return "$status"
        fi
    fi
    # Checkpoint each successful step so an interrupted setup can resume.
    : > "$marker_file"
}

is_db migrate

state_dir="${TTN_LW_BLOB_LOCAL_DIRECTORY:-/srv/ttn-lorawan/public/blob}"
mkdir -p "$state_dir"

result_file="$(mktemp)"
trap 'rm -f "$result_file"' EXIT
trap 'exit 1' HUP INT TERM

ensure_resource 'TTN administrator' .sbms-admin-initialized user_id_taken create-admin-user --id admin \
    --email "${TTS_BOOTSTRAP_EMAIL:-admin@127.0.0.1}" \
    --password "${TTS_BOOTSTRAP_PASSWORD:-admin}"

ensure_resource 'Console OAuth client' .sbms-console-initialized client_id_taken create-oauth-client \
    --id console --name Console --owner admin \
    --secret "$TTN_LW_CONSOLE_OAUTH_CLIENT_SECRET" \
    --redirect-uri "$TTN_LW_CONSOLE_UI_CANONICAL_URL/oauth/callback" \
    --redirect-uri /console/oauth/callback \
    --logout-redirect-uri "$TTN_LW_CONSOLE_UI_CANONICAL_URL" \
    --logout-redirect-uri /console

echo 'The Things Stack initialization is complete.'
