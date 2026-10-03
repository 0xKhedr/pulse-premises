from __future__ import annotations

import csv
from collections.abc import Mapping
from typing import Any

SECRET_KEY_PARTS = (
    "app_key",
    "api_key",
    "password",
    "root_key",
    "secret",
    "token",
)


def redact_secrets(value: Any) -> Any:
    """Return an export-safe copy with credential-shaped fields removed."""
    if isinstance(value, Mapping):
        redacted = {}
        for key, child in value.items():
            normalized_key = str(key).strip().lower()
            if any(part in normalized_key for part in SECRET_KEY_PARTS):
                redacted[key] = "***hidden***"
            else:
                redacted[key] = redact_secrets(child)
        return redacted
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_secrets(item) for item in value)
    return value


def csv_safe(value: Any) -> Any:
    """Prevent CSV cells from being interpreted as spreadsheet formulas."""
    if not isinstance(value, str):
        return value
    if value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


class SafeDictWriter(csv.DictWriter):
    def writerow(self, rowdict):
        return super().writerow({key: csv_safe(value) for key, value in rowdict.items()})
