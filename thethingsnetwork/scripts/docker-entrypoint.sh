#!/bin/sh
set -eu

# Keep the mounted template unchanged and restrict access to the rendered config
umask 077
envsubst < /config/ttn-lw-stack-docker.yml > /tmp/ttn-lw-stack.yml

if [ "${1:-}" = start ]; then
    stack-init.sh
fi

exec ttn-lw-stack -c /tmp/ttn-lw-stack.yml "$@"
