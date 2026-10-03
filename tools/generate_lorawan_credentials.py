from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

from dotenv import set_key

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import Settings
from app.services.lorawan_identity import dev_eui_namespace_mask, normalize_hex

settings = Settings(_env_file=PROJECT_ROOT / ".env")


def byte_list(hex_value: str, *, reverse: bool = False) -> list[str]:
    values = [hex_value[index : index + 2] for index in range(0, len(hex_value), 2)]
    if reverse:
        values.reverse()
    return [f"0x{value}" for value in values]


def wrap(values: list[str], indent: str = "    ") -> str:
    lines = []
    for index in range(0, len(values), 8):
        # Keep both the C initializer comma and the preprocessor line
        # continuation when a value spans more than one generated line.
        suffix = ", \\" if index + 8 < len(values) else ""
        lines.append(indent + ", ".join(values[index : index + 8]) + suffix)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate the local firmware header for shared LoRaWAN credentials."
    )
    parser.add_argument(
        "--generate-app-key",
        action="store_true",
        help="Generate a new shared AppKey, save it to .env, and never print it.",
    )
    args = parser.parse_args()
    try:
        join_eui = normalize_hex(settings.JOIN_EUI, 16, "JOIN_EUI")
        if args.generate_app_key:
            app_key = secrets.token_hex(16).upper()
            env_path = PROJECT_ROOT / ".env"
            set_key(str(env_path), "LORAWAN_APP_KEY", app_key, quote_mode="never")
            print("Generated a new shared AppKey and stored it in .env (value hidden).")
        else:
            app_key = normalize_hex(settings.LORAWAN_APP_KEY, 32, "LORAWAN_APP_KEY")
        namespace_mask = dev_eui_namespace_mask(settings.TTN_APP_ID)
        content = (
            "#pragma once\n\n"
            "// Generated from .env; shared by every SBMS LoRaWAN node.\n"
            "#define SBMS_JOIN_EUI_LSB_BYTES \\\n"
            f"{wrap(byte_list(join_eui, reverse=True))}\n\n"
            "#define SBMS_APP_KEY_BYTES \\\n"
            f"{wrap(byte_list(app_key))}\n\n"
            "// App-specific mask prevents the same hardware MAC from colliding across TTN apps.\n"
            "#define SBMS_DEV_EUI_XOR_LSB_BYTES \\\n"
            f"{wrap(byte_list(namespace_mask, reverse=True))}\n"
        )
        # Arduino sketch folders are self-contained, so every sketch that joins
        # the network needs its own copy of the header rather than a shared one.
        # Each is gitignored.
        for sketch in ("lilygo_cayenne_lpp_node", "espcam_lorawan_node"):
            target = (
                PROJECT_ROOT / "firmware" / sketch / "lorawan_credentials.h"
            )
            if not target.parent.is_dir():
                continue
            target.write_text(content, encoding="utf-8")
            print(f"Generated {target}")
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
