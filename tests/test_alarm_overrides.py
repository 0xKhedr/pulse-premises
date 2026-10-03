"""
Per-device alarm threshold overrides.

The point of an override is that it is a sparse patch: a device changes one
number and keeps inheriting everything else. Most of what is worth testing is
therefore about what is *not* changed, and about refusing a patch that the
evaluator could not act on.
"""

from __future__ import annotations

import sqlite3

import pytest

from app.main import build_device_alarm_profile, evaluate_profile_alarm_rules
from app.services.alarm_overrides import (
    apply_overrides,
    describe_rule,
    effective_rule,
    load_overrides,
    normalize_override_payload,
)

HIGH_TEMP = {
    "id": 1,
    "rule_code": "CAYENNE_HIGH_TEMP",
    "field_key": "temperature",
    "operator": ">",
    "threshold_value": 30.0,
    "threshold_value_2": None,
    "severity": "warning",
    "alarm_type": "sensor_alarm",
    "enabled": 1,
}

COMFORT_BAND = {
    "id": 2,
    "rule_code": "COMFORT_BAND",
    "field_key": "temperature",
    "operator": "outside",
    "threshold_value": 18.0,
    "threshold_value_2": 24.0,
    "severity": "info",
    "enabled": 1,
}

MOTION = {
    "id": 3,
    "rule_code": "CAYENNE_MOTION_DETECTED",
    "field_key": "motion",
    "operator": "==",
    "threshold_value": None,
    "threshold_value_2": None,
    "expected_boolean": 1,
    "severity": "warning",
    "enabled": 1,
}


def test_no_override_leaves_the_rule_alone():
    assert effective_rule(HIGH_TEMP, None)["threshold_value"] == 30.0


def test_override_moves_only_what_it_sets():
    merged = effective_rule(
        HIGH_TEMP, {"threshold_value": 24.0, "threshold_value_2": None,
                    "enabled": None}
    )

    assert merged["threshold_value"] == 24.0
    # Everything the profile is responsible for survives untouched.
    assert merged["operator"] == ">"
    assert merged["severity"] == "warning"
    assert merged["field_key"] == "temperature"
    assert merged["enabled"] == 1


def test_the_profile_rule_is_not_mutated():
    """The profile is loaded once per uplink and read by several consumers."""
    effective_rule(HIGH_TEMP, {"threshold_value": 5.0})
    assert HIGH_TEMP["threshold_value"] == 30.0


def test_muting_applies_only_to_this_device():
    merged = effective_rule(HIGH_TEMP, {"enabled": 0})
    assert merged["enabled"] == 0
    assert HIGH_TEMP["enabled"] == 1


def test_a_rule_can_be_forced_on_for_one_device():
    off_everywhere = dict(HIGH_TEMP, enabled=0)
    assert effective_rule(off_everywhere, {"enabled": 1})["enabled"] == 1


def test_one_bound_of_a_range_can_be_raised():
    merged = effective_rule(COMFORT_BAND, {"threshold_value_2": 27.0})
    assert merged["threshold_value"] == 18.0
    assert merged["threshold_value_2"] == 27.0


def test_an_override_with_no_matching_rule_is_reported_not_applied():
    rules, stale = apply_overrides(
        [HIGH_TEMP], {"RULE_FROM_AN_OLD_PROFILE": {"threshold_value": 1.0}}
    )

    # The device keeps reporting through a profile change; the leftover is
    # surfaced rather than silently obeyed or silently dropped.
    assert rules[0]["threshold_value"] == 30.0
    assert stale == ["RULE_FROM_AN_OLD_PROFILE"]


def test_a_threshold_on_a_boolean_rule_is_refused():
    """Accepting it would store a number the evaluator can never consult."""
    with pytest.raises(ValueError, match="no numeric threshold"):
        normalize_override_payload(MOTION, {"threshold_value": 1})


def test_a_boolean_rule_can_still_be_muted():
    assert normalize_override_payload(MOTION, {"enabled": False}) == {
        "threshold_value": None,
        "threshold_value_2": None,
        "enabled": 0,
        "note": None,
    }


def test_a_second_threshold_on_a_single_bound_rule_is_refused():
    with pytest.raises(ValueError, match="single threshold"):
        normalize_override_payload(
            HIGH_TEMP, {"threshold_value": 24, "threshold_value_2": 30}
        )


def test_a_range_may_be_completed_from_the_profile():
    """Overriding one bound is legitimate; the other comes from the profile."""
    assert normalize_override_payload(
        COMFORT_BAND, {"threshold_value_2": 27}
    )["threshold_value_2"] == 27.0


def test_a_range_that_would_end_up_half_defined_is_refused():
    open_ended = dict(COMFORT_BAND, threshold_value_2=None)
    with pytest.raises(ValueError, match="both bounds"):
        normalize_override_payload(open_ended, {"threshold_value": 15})


@pytest.mark.parametrize("value", ["", "warm", float("inf"), float("nan")])
def test_an_unusable_threshold_is_refused(value):
    if value == "":
        # Empty means "inherit", which on its own changes nothing.
        with pytest.raises(ValueError, match="changes nothing"):
            normalize_override_payload(HIGH_TEMP, {"threshold_value": value})
    else:
        with pytest.raises(ValueError):
            normalize_override_payload(HIGH_TEMP, {"threshold_value": value})


def test_an_override_that_changes_nothing_is_refused():
    """An all-NULL row would look like a customisation and behave as none."""
    with pytest.raises(ValueError, match="changes nothing"):
        normalize_override_payload(HIGH_TEMP, {"note": "just a note"})


def test_describe_rule_shows_default_and_result_together():
    described = describe_rule(HIGH_TEMP, {"threshold_value": 24.0})

    assert described["profile"]["threshold_value"] == 30.0
    assert described["effective"]["threshold_value"] == 24.0
    assert described["overridden"] is True
    assert described["numeric"] is True
    assert described["needs_second_threshold"] is False


def test_a_muted_rule_is_reported_as_false_rather_than_zero():
    """
    SQLite stores the tri-state as 0/1, and 0 is falsy in Python and in
    JavaScript alike. A client deciding between "muted" and "inherited" by
    testing for null would read a mute as an inherit.
    """
    described = describe_rule(HIGH_TEMP, {"enabled": 0})
    assert described["override"]["enabled"] is False

    described = describe_rule(HIGH_TEMP, {"enabled": 1})
    assert described["override"]["enabled"] is True


def test_an_inherited_enable_stays_null():
    described = describe_rule(HIGH_TEMP, {"threshold_value": 24.0,
                                          "enabled": None})
    assert described["override"]["enabled"] is None


def test_describe_rule_marks_a_boolean_rule_as_not_numeric():
    described = describe_rule(MOTION, None)
    assert described["numeric"] is False
    assert described["override"] is None
    assert described["overridden"] is False


def test_an_override_changes_whether_an_alarm_fires():
    """The end-to-end point of the feature, at the evaluator's own boundary."""
    profile = {
        "id": 1,
        "profile_code": "TEST_V1",
        "profile_name": "Test",
        "profile_version": 1,
        "rules": [HIGH_TEMP],
    }
    telemetry = {"temperature": 26.0}

    assert evaluate_profile_alarm_rules(profile, telemetry)["alarms"] == []

    patched, _ = apply_overrides([HIGH_TEMP], {
        "CAYENNE_HIGH_TEMP": {"threshold_value": 24.0}
    })
    alarms = evaluate_profile_alarm_rules(
        dict(profile, rules=patched), telemetry
    )["alarms"]

    assert len(alarms) == 1
    # History records what actually fired, not the profile's number.
    assert alarms[0]["threshold_value"] == 24.0


def test_a_missing_overrides_table_falls_back_to_the_profile():
    """
    An uplink must survive a database that has not been migrated yet.

    A device reporting a reading matters more than a customisation being
    applied to it, so a failed read degrades to the profile's own thresholds
    rather than dropping the uplink.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        assert load_overrides(conn, "node-1") == {}

        profile = {"id": 1, "profile_code": "TEST_V1", "rules": [HIGH_TEMP]}
        assert build_device_alarm_profile(conn, "node-1", profile) is profile
    finally:
        conn.close()
