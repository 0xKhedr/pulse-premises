"""
Register fields the first time a device reports them.

A decoded uplink is self-describing: it arrives as named values, and the
webhook stores all of them whether or not a profile declares them. What a
declared field adds is everything around the value -- a human label instead of
a raw key, a unit, display order, floor and dashboard visibility, and something
for an alarm rule's field_key to point at.

Learning closes that gap from the first uplink, so a new kind of device is
usable without anyone authoring a profile by hand. It deliberately extends the
profile a device is already on rather than minting a new profile per device
type: a node that reports a field only sometimes -- a camera with nothing to
read, say -- would otherwise generate a fresh profile every time its payload
shape changed.

The input is radio traffic, so this is treated as a trust boundary. Only keys
that look like identifiers are considered, learned fields are always optional
and nullable so no existing device can be made invalid by one, an authored
field is never overwritten, and a profile may only learn so many before it
stops. Everything here is pure; the caller does the writing.
"""

from __future__ import annotations

import re

# Keys that carry meaning to the platform rather than to a device. They are
# stored alongside telemetry but are not sensor readings.
SYSTEM_KEYS = frozenset({
    "alarm_active", "alarm_message", "battery_percent", "battery_status",
    "battery_voltage", "node_type", "payload_version", "power_source",
    "profile_alarm_active_count", "profile_alarm_rule_codes", "profile_code",
    "profile_version", "received_by_gateways", "gateway_id", "rssi", "snr",
    "signal_status", "spreading_factor", "bandwidth", "frequency",
    "consumed_airtime", "local_updated_at", "updated_at",
})

# A raw Cayenne channel: temperature_1, relative_humidity_2, and so on. These
# are kept in telemetry for traceability but the canonical key beside them is
# the one worth declaring, so learning both would double every profile.
_CAYENNE_CHANNEL = re.compile(r"^[a-z_]+_\d+$")

# Conservative on purpose: a field key becomes a column-like identifier in the
# UI and in rule definitions.
_VALID_KEY = re.compile(r"^[a-z][a-z0-9_]{1,39}$")

# How many fields one profile may learn. Reached only by a device sending
# nonsense, since a real payload has a stable shape.
MAX_LEARNED_FIELDS = 40


def _looks_learnable(key: str) -> bool:
    if key in SYSTEM_KEYS:
        return False
    if _CAYENNE_CHANNEL.match(key):
        return False
    return bool(_VALID_KEY.match(key))


def infer_data_type(value: object) -> str | None:
    """Map a reported value onto a profile data_type, or None if unusable."""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    return None


def humanize(key: str) -> str:
    """A first-guess label. An administrator can correct it and it will stick."""
    words = [part for part in key.split("_") if part]
    return " ".join(word.upper() if len(word) <= 3 else word.capitalize()
                    for word in words) or key


def plan_learned_fields(
    telemetry: dict,
    declared_keys: set[str],
    *,
    already_learned: int = 0,
    max_fields: int = MAX_LEARNED_FIELDS,
) -> list[dict]:
    """
    Decide which reported fields are worth declaring on the profile.

    Returns one descriptor per field to add, in a stable order so two uplinks
    carrying the same new fields produce the same result.
    """
    if already_learned >= max_fields:
        return []

    declared = {str(key).strip().lower() for key in declared_keys}
    budget = max_fields - already_learned
    planned: list[dict] = []

    for key in sorted(telemetry):
        if len(planned) >= budget:
            break
        clean = str(key).strip().lower()
        if clean in declared or not _looks_learnable(clean):
            continue
        data_type = infer_data_type(telemetry[key])
        if data_type is None:
            continue
        planned.append({
            "field_key": clean,
            "label": humanize(clean),
            "data_type": data_type,
            # Always optional and nullable: a learned field must never make an
            # existing device's payload fail validation.
            "required": 0,
            "nullable": 1,
            "auto_learned": 1,
            "visible_floor": 1,
            "visible_dashboard": 1,
        })
    return planned
