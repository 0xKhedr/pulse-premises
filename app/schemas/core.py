from __future__ import annotations

from pydantic import BaseModel


class Device(BaseModel):
    """
    Provisioning request model.

    chip_mac and node_type are always required.
    All other fields are optional to support both
    profile-driven and legacy provisioning flows.
    """

    # --- ESP32 identity ---
    chip_mac: str
    node_type: str

    # --- Canonical location (preferred: room_id) ---
    room_id: int | None = None

    # --- Temporary compatibility fields (derived from room_id when present) ---
    building: str | None = None
    floor: str | None = None
    room: str | None = None

    # --- Stable sensor-profile identity ---
    profile_id: int | None = None
    profile_code: str | None = None
    profile_version: int | None = None

    # --- Payload contract selected by the profile ---
    payload_version: int | None = None
    payload_encoder_key: str | None = None
    uplink_interval_seconds: int | None = None
    firmware_version: str | None = None

    # --- Device capabilities (derived from profile when using profile flow) ---
    capabilities: list[str] | None = None

    # --- Display / floor-map metadata ---
    label: str | None = None
    x: int | None = None
    y: int | None = None
    icon_type: str | None = None


class ProfileAlarmTemplateValues(dict):
    """
    Preserve an unknown placeholder instead of causing the alarm
    engine to fail because of one custom message template.
    """

    def __missing__(self, key: str) -> str:  # type: ignore[override]
        return "{" + str(key) + "}"
