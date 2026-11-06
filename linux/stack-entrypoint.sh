#!/bin/sh
set -eu

if [ "${1:-}" = start ]; then
    /setup/stack-init.sh
fi

# Replace the shell so the server receives Docker stop signals directly.
exec ttn-lw-stack -c /config/ttn-lw-stack.yml "$@"
