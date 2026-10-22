"""Generate a fresh building deployment without changing the developer's .env.

Uses only the Python standard library, so it can run on a new Linux server.
The generated stack file is JSON (a YAML subset understood by The Things Stack).
Secrets live in .env.building; the stack configuration contains public URLs only.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def validate_host(host: str) -> str:
    """Require a LAN-reachable IPv4 address or DNS hostname, without a port."""
    if host.lower() == "localhost" or len(host) > 253:
        raise ValueError("Use the building server's LAN IPv4 address or DNS name")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if re.fullmatch(r"[0-9.]+", host):
            raise ValueError("Invalid IPv4 address") from None
        labels = host.rstrip(".").split(".")
        if not all(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                   for label in labels):
            raise ValueError("Host must be a DNS name or IPv4 address, without a scheme or port")
    else:
        if address.version != 4 or address.is_loopback or address.is_unspecified or address.is_multicast:
            raise ValueError("Use a reachable, unicast LAN IPv4 address")
    return host


def stack_config(host: str) -> dict:
    base = f"http://{validate_host(host)}:1885"
    api = {"base-url": f"{base}/api/v3"}
    return {
        "log": {"level": "info"},
        "skip-version-check": True,
        "http": {"listen": ":1885", "listen-tls": "", "health": {"enable": True},
                 "metrics": {"enable": False}, "pprof": {"enable": False}},
        "grpc": {"listen": ":1884", "listen-tls": "", "allow-insecure-for-credentials": True},
        "cluster": {"tls": False},
        "is": {"oauth": {"ui": {"canonical-url": f"{base}/oauth", "is": api}}},
        "console": {
            "ui": {"canonical-url": f"{base}/console", "account-url": f"{base}/oauth",
                   **{component: api for component in ("is", "gs", "ns", "as", "js", "gcs")}},
            "oauth": {"authorize-url": f"{base}/oauth/authorize",
                      # Token exchange runs inside the stack container.
                      "token-url": "http://localhost:1885/oauth/token",
                      "logout-url": f"{base}/oauth/logout", "client-id": "console"},
        },
        "gs": {
            "require-registered-gateways": True,
            "mqtt": {"listen-tls": "", "public-address": f"{host}:1882", "public-tls-address": ""},
            "mqtt-v2": {"listen-tls": "", "public-address": f"{host}:1881", "public-tls-address": ""},
            "basic-station": {"listen": ":1887", "listen-tls": "", "use-traffic-tls-address": False},
        },
        "as": {
            "mqtt": {"listen-tls": "", "public-address": f"{host}:1883", "public-tls-address": ""},
            "webhooks": {"downlink": {"public-address": f"{base}/api/v3", "public-tls-address": ""}},
        },
        "gcs": {"basic-station": {"default": {"lns-uri": f"ws://{host}:1887"}}},
        "blob": {"provider": "local", "local": {"directory": "/srv/ttn-lorawan/public/blob"}},
        # Load plans shipped in the image, allowing operation without Internet.
        "frequency-plans": {"config-source": "directory", "directory": "/srv/ttn-lorawan/lorawan-frequency-plans"},
    }


def prepare(host: str, admin_email: str, output_dir: Path, image: str) -> tuple[Path, Path]:
    validate_host(host)
    if not re.fullmatch(r"[A-Za-z0-9_.+%-]+@[A-Za-z0-9.-]+", admin_email):
        raise ValueError("Provide an administrator email address")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._/@:-]+", image):
        raise ValueError("Provide a Docker image reference without spaces or shell characters")
    env_path = output_dir / ".env.building"
    config_path = output_dir / "deploy/stack/ttn-lw-stack.yml"
    if env_path.exists() or config_path.exists():
        raise FileExistsError("Deployment files already exist; edit them in place to preserve keys and passwords")
    template = (ROOT / ".env.building.example").read_text(encoding="utf-8")
    values = {
        "TTS_HOST": host,
        "ADMIN_EMAIL": admin_email,
        "ADMIN_PASSWORD": secrets.token_urlsafe(32),
        "SBMS_IMAGE": image,
        "TTS_POSTGRES_PASSWORD": secrets.token_hex(32),
        "TTS_CONSOLE_SECRET": secrets.token_hex(32),
        "TTS_COOKIE_BLOCK_KEY": secrets.token_hex(32),
        "TTS_COOKIE_HASH_KEY": secrets.token_hex(64),
        "TTS_CLUSTER_KEY": secrets.token_hex(16),
        "TTN_WEBHOOK_SECRET": secrets.token_hex(32),
        "LORAWAN_APP_KEY": secrets.token_hex(16).upper(),
    }
    lines = []
    for line in template.splitlines():
        key = line.partition("=")[0]
        lines.append(f"{key}={values[key]}" if key in values else line)
    output_dir.mkdir(parents=True, exist_ok=True)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents accidentally rotating a live deployment's keys.
    descriptor = os.open(env_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        stream.write("\n".join(lines) + "\n")
    with config_path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(stack_config(host), stream, indent=2)
        stream.write("\n")
    # This file has no secrets and must be readable by the stack's uid 886.
    config_path.chmod(0o644)
    return env_path, config_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="Building server's static LAN IPv4 or DNS name")
    parser.add_argument("--admin-email", required=True)
    parser.add_argument("--image", default="ghcr.io/0xkhedr/pulse-premises:latest")
    parser.add_argument("--output-dir", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        env_path, config_path = prepare(args.host, args.admin_email, args.output_dir, args.image)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"ERROR: {exc}\n")
    print(f"Created {env_path} and {config_path}")
    print("The SBMS administrator password is in .env.building (not printed).")
    print("Next: follow deploy/README.md to initialize The Things Stack and its webhook.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
