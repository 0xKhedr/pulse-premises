#!/usr/bin/env bash
# Compatibility shortcut. Initialization now runs in the stack entrypoint.
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ $# -gt 1 ]]; then
    echo "Usage: bash linux/initialize-building.sh [stack-admin-email] (default: admin@127.0.0.1)" >&2
    exit 1
fi
if [[ $# -eq 1 ]]; then
    export TTS_BOOTSTRAP_EMAIL="$1"
fi
docker compose up -d --build --wait stack
