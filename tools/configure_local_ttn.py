from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from dotenv import set_key

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    api_key = os.environ.get("SBMS_LOCAL_TTN_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("SBMS_LOCAL_TTN_API_KEY is required")

    env_path = PROJECT_ROOT / ".env"
    backup_dir = (
        PROJECT_ROOT
        / "backups"
        / f"local-ttn-config-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    )
    backup_dir.mkdir(parents=True, exist_ok=False)
    if env_path.exists():
        shutil.copy2(env_path, backup_dir / ".env")

    # Host-side tools use localhost. docker-compose overrides only the
    # container value with host.docker.internal.
    set_key(str(env_path), "TTN_BASE_URL", "http://localhost:1885", quote_mode="never")
    set_key(str(env_path), "TTN_APP_ID", "smart-building-lora-2", quote_mode="never")
    set_key(str(env_path), "TTN_API_KEY", api_key, quote_mode="never")

    print("Configured the local TTN endpoint and stored its API key (value hidden).")
    print(f"Previous environment backed up in {backup_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
