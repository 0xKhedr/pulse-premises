"""
Let one device deviate from its sensor profile's alarm thresholds.

A profile's alarm rules are shared by every device on it, which is the point
of a profile -- twenty identical sensors should not need twenty copies of the
same rule. But a shared rule has no room for the one device that lives
somewhere unusual: the cupboard that genuinely runs at 34 C, the motion sensor
by a fire door that would otherwise alarm all day.

An override is a sparse patch over one rule rather than a copy of it. Only two
things can be changed: where the rule trips, and whether it applies to this
device at all. The operator, the field, the severity and the message stay with
the profile, so a device can never quietly come to mean something different
from what its profile documents. Anything left NULL is inherited, which is
what lets a device raise one bound of a range and follow the profile for the
rest.

Everything above ``load_overrides`` is pure, so the merge and the validation
can be tested without a database or an uplink.
"""

from __future__ import annotations

import math
import sqlite3

# Operators that compare a number, and so have a threshold worth overriding.
NUMERIC_OPERATORS = frozenset({">", ">=", "<", "<=", "between", "outside"})

# Operators that need both bounds. A device may override either one, but the
# pair that results must still be complete or the rule cannot be evaluated.
RANGE_OPERATORS = frozenset({"between", "outside"})

# A note is a reminder of why this device is different, not a record.
MAX_NOTE_LENGTH = 200


def rule_operator(rule: dict) -> str:
    return str(rule.get("operator") or "").strip().lower()


def rule_is_numeric(rule: dict) -> bool:
    """Whether this rule trips on a number, and so has a threshold to move."""
    return rule_operator(rule) in NUMERIC_OPERATORS


def rule_needs_second_threshold(rule: dict) -> bool:
    return rule_operator(rule) in RANGE_OPERATORS


def _inherit(override_value, profile_value):
    """NULL in an override means "inherit this one", not "clear it"."""
    return profile_value if override_value is None else override_value


def effective_rule(rule: dict, override: dict | None) -> dict:
    """
    Return the rule as this device should be judged by.

    The result is a new dict; the profile's own rule is never mutated, because
    the profile is loaded once per uplink and handed to several consumers.
    """
    if not override:
        return dict(rule)

    patched = dict(rule)
    patched["threshold_value"] = _inherit(
        override.get("threshold_value"), rule.get("threshold_value")
    )
    patched["threshold_value_2"] = _inherit(
        override.get("threshold_value_2"), rule.get("threshold_value_2")
    )

    # enabled is tri-state here: inherit, force on, or mute for this device.
    # Forcing on is deliberate -- it lets a rule stay off across the estate
    # while being watched on the one device that needs it.
    override_enabled = override.get("enabled")
    if override_enabled is not None:
        patched["enabled"] = 1 if override_enabled else 0

    patched["override_applied"] = True
    return patched


def apply_overrides(rules: list, overrides: dict) -> tuple[list, list]:
    """
    Merge a device's overrides into its profile's rules.

    Returns the rules to evaluate and the codes of any overrides that matched
    no rule. A stale override is inert rather than an error: moving a device to
    another profile should change what it reports, not break its uplinks, and
    the leftovers are surfaced in the UI to be cleared deliberately.
    """
    if not overrides:
        return [dict(rule) for rule in rules], []

    merged = []
    matched = set()
    for rule in rules:
        code = str(rule.get("rule_code") or "").strip().upper()
        override = overrides.get(code)
        if override:
            matched.add(code)
        merged.append(effective_rule(rule, override))

    stale = sorted(set(overrides) - matched)
    return merged, stale


def _clean_number(value, label: str):
    """Reject anything that would make a rule impossible to evaluate."""
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a number")
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite number")
    return number


def normalize_override_payload(rule: dict, payload: dict) -> dict:
    """
    Validate a submitted override against the rule it patches.

    Raises ValueError with a message meant for the person editing it. The
    checks are about what the evaluator can actually act on: a threshold on a
    rule that compares text is not a stricter setting, it is a value that would
    be silently ignored, which is worse than a refusal.
    """
    if not isinstance(payload, dict):
        raise ValueError("An override object is required")

    threshold = _clean_number(payload.get("threshold_value"), "threshold_value")
    threshold_2 = _clean_number(
        payload.get("threshold_value_2"), "threshold_value_2"
    )

    raw_enabled = payload.get("enabled")
    if raw_enabled is None or raw_enabled == "":
        enabled = None
    elif isinstance(raw_enabled, bool):
        enabled = 1 if raw_enabled else 0
    elif str(raw_enabled).strip().lower() in {"1", "true", "yes", "on"}:
        enabled = 1
    elif str(raw_enabled).strip().lower() in {"0", "false", "no", "off"}:
        enabled = 0
    else:
        raise ValueError("enabled must be true, false, or null to inherit")

    numeric = rule_is_numeric(rule)
    if not numeric and (threshold is not None or threshold_2 is not None):
        raise ValueError(
            f"The {rule.get('rule_code')} rule compares "
            f"'{rule_operator(rule)}', which has no numeric threshold to "
            "override. Only muting it for this device is available."
        )

    if numeric and not rule_needs_second_threshold(rule):
        if threshold_2 is not None:
            raise ValueError(
                f"The {rule.get('rule_code')} rule takes a single threshold"
            )

    # A range needs both bounds, but either may come from the profile, so the
    # check is on what the evaluator would end up with rather than on what was
    # submitted.
    if numeric and rule_needs_second_threshold(rule):
        merged_low = _inherit(threshold, rule.get("threshold_value"))
        merged_high = _inherit(threshold_2, rule.get("threshold_value_2"))
        if (merged_low is None) != (merged_high is None):
            raise ValueError(
                f"The {rule.get('rule_code')} rule needs both bounds; "
                "supply the missing one or revert to the profile"
            )

    note = str(payload.get("note") or "").strip()
    if len(note) > MAX_NOTE_LENGTH:
        raise ValueError(f"note must be {MAX_NOTE_LENGTH} characters or fewer")

    if threshold is None and threshold_2 is None and enabled is None:
        raise ValueError(
            "This override changes nothing. Delete it to return the device to "
            "its profile."
        )

    return {
        "threshold_value": threshold,
        "threshold_value_2": threshold_2,
        "enabled": enabled,
        "note": note or None,
    }


def describe_rule(rule: dict, override: dict | None) -> dict:
    """
    Present one rule for editing: the profile default, the patch, the result.

    All three are returned together because the question being asked of this
    screen is never "what is the threshold" but "how far is this device from
    the rest, and why".
    """
    merged = effective_rule(rule, override)
    numeric = rule_is_numeric(rule)

    # SQLite hands back 0 and 1 for the tri-state column, and 0 is falsy in
    # both Python and JavaScript. Presented raw, a muted rule is indistinguishable
    # from an unset one to any consumer testing for null, so the boundary is
    # where it becomes a real boolean -- or stays None, meaning inherit.
    override_view = None
    if override:
        override_view = dict(override)
        stored_enabled = override_view.get("enabled")
        override_view["enabled"] = (
            None if stored_enabled is None else bool(stored_enabled)
        )

    return {
        "rule_code": rule.get("rule_code"),
        "field_key": rule.get("field_key"),
        "operator": rule_operator(rule),
        "severity": rule.get("severity"),
        "alarm_type": rule.get("alarm_type"),
        "numeric": numeric,
        "needs_second_threshold": numeric and rule_needs_second_threshold(rule),
        "profile": {
            "threshold_value": rule.get("threshold_value"),
            "threshold_value_2": rule.get("threshold_value_2"),
            "enabled": bool(rule.get("enabled")),
        },
        "override": override_view,
        "effective": {
            "threshold_value": merged.get("threshold_value"),
            "threshold_value_2": merged.get("threshold_value_2"),
            "enabled": bool(merged.get("enabled")),
        },
        "overridden": bool(override),
    }


# --------------------------------------------------------------------------
# Storage. Kept below the pure functions above, which need no database.
# --------------------------------------------------------------------------


def load_overrides(conn: sqlite3.Connection, device_id: str) -> dict:
    """
    Read one device's overrides, keyed by rule code.

    Never fatal. This runs on the uplink path, where a device reporting a
    reading matters more than a customisation being applied to it: if the read
    fails the device falls back to its profile's thresholds, which is the
    behaviour it had before any override existed.
    """
    clean_device_id = str(device_id or "").strip()
    if not clean_device_id:
        return {}
    try:
        rows = conn.execute(
            """
            SELECT rule_code, threshold_value, threshold_value_2, enabled,
                   note, updated_at, updated_by
            FROM device_alarm_overrides
            WHERE device_id = ?
            """,
            (clean_device_id,),
        ).fetchall()
    except sqlite3.Error:
        return {}

    overrides = {}
    for row in rows:
        record = dict(row)
        code = str(record.pop("rule_code") or "").strip().upper()
        if code:
            overrides[code] = record
    return overrides
