#!/bin/sh
set -eu

if [ "${1:-}" = start ]; then
    /setup/stack-init.sh
    # Capture JSON stderr via a FIFO while preserving direct stop signals to TTN.
    # The reader and heartbeat exit when the container stops. The journal is
    # outside the public blob directory and mounted read-only in SBMS.
    if [ "${SBMS_GATEWAY_JOURNAL:-false}" = true ]; then
        umask 022
        journal_tmp="$(mktemp -d /tmp/ttn-log.XXXXXX)"
        fifo="$journal_tmp/stderr"
        mkfifo "$fifo"
        boot="$(cat /proc/sys/kernel/random/uuid)"
        if [ -f /var/log/ttn/attempts.current ]; then
            mv -f /var/log/ttn/attempts.current /var/log/ttn/attempts.previous
        fi
        awk -v boot="$boot" -f /setup/stack-journal.awk < "$fifo" >&2 &
        (while true; do touch /var/log/ttn/heartbeat; sleep 10; done) &
        exec ttn-lw-stack -c /config/ttn-lw-stack.yml --log.format json "$@" 2> "$fifo"
    fi
fi

# Replace the shell so the server receives Docker stop signals directly.
exec ttn-lw-stack -c /config/ttn-lw-stack.yml "$@"
