from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any


def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def build_device_state(
    device: Mapping[str, Any],
    *,
    now: datetime | None = None,
    stale_after_seconds: int = 300,
    offline_after_seconds: int = 86400,
) -> dict[str, Any]:
    """Return the UI-facing commissioning and connection state for a device."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    else:
        current = current.astimezone(timezone.utc)

    last_seen = _parse_timestamp(
        device.get("last_seen") or device.get("telemetry_updated_at")
    )
    age_seconds = None
    if last_seen is None:
        connection_state = "never_connected"
    else:
        age_seconds = max(0, int((current - last_seen).total_seconds()))
        if age_seconds >= offline_after_seconds:
            connection_state = "offline"
        elif age_seconds >= stale_after_seconds:
            connection_state = "stale"
        else:
            connection_state = "online"

    commissioned = bool(device.get("is_placed") and device.get("floor_id"))
    return {
        "connection_state": connection_state,
        "monitoring_state": "operational" if commissioned else "discovery",
        "last_seen_age_seconds": age_seconds,
        "commissioned": commissioned,
    }
