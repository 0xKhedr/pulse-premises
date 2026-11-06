#!/bin/sh
set -eu
umask 077

is_db() {
    ttn-lw-stack -c /tmp/ttn-lw-stack.yml is-db "$@"
}

is_db migrate

initialized="/state/.initialized"

if [ -f "$initialized" ]; then
    exit 0
fi

is_db create-admin-user --email "admin@${TTN_HOST}" --password "$TTN_ADMIN_PASSWORD"
is_db create-oauth-client --owner admin --secret console --redirect-uri /console/oauth/callback

touch "$initialized"
