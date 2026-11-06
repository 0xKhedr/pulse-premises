"""Prepare configuration and start SBMS plus The Things Stack on Windows or Linux."""

from __future__ import annotations

import argparse
import shutil
import subprocess

try:
    from .prepare_building import ROOT, prepare
except ImportError:
    from prepare_building import ROOT, prepare


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="localhost", help="Server IPv4 or DNS name (default: localhost)")
    parser.add_argument("--admin-email", help="SBMS admin email (default: admin@{host})")
    parser.add_argument("--prepare-only", action="store_true", help="Prepare configuration without starting Docker")
    args = parser.parse_args()
    try:
        if not args.prepare_only:
            if not shutil.which("docker"):
                raise ValueError("Install Docker with Compose and start Docker before running setup.")
            subprocess.run(["docker", "compose", "version"], cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
        prepare(host=args.host, admin_email=args.admin_email, output_dir=ROOT)
        print("Generated .env and stack configuration, overwriting existing files.", flush=True)
        if args.prepare_only:
            print("Configuration ready. Start with: docker compose up -d --build --wait")
            return 0
        subprocess.run(["docker", "compose", "config", "--quiet"], cwd=ROOT, check=True)
        subprocess.run(["docker", "compose", "up", "-d", "--build", "--wait", "--wait-timeout", "300"],
                       cwd=ROOT, check=True)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"ERROR: {exc}\n")
    print("Setup complete. SBMS and The Things Stack are running.")
    print("Credentials and host settings are in .env. See deploy/README.md for gateway and webhook setup.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
