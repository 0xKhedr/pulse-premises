"""
Registering fields the first time a device reports them.

The input is radio traffic and the output shapes a profile definition, so the
tests lean on what must be refused as much as what is learned.
"""

from __future__ import annotations

import pytest

from app.services.profile_learning import (
    MAX_LEARNED_FIELDS,
    humanize,
    infer_data_type,
    plan_learned_fields,
)


def test_a_genuinely_new_field_is_planned():
    planned = plan_learned_fields(
        {"recognized_text": "GATE 7", "temperature": 21.5},
        declared_keys={"temperature"},
    )
    assert [f["field_key"] for f in planned] == ["recognized_text"]
    assert planned[0]["data_type"] == "string"
    assert planned[0]["label"] == "Recognized Text"


def test_learned_fields_are_always_optional_and_nullable():
    """
    A learned field must never make an existing device's payload invalid. Every
    other device on the profile carries on reporting exactly what it did.
    """
    planned = plan_learned_fields({"new_metric": 1.0}, declared_keys=set())
    assert planned[0]["required"] == 0
    assert planned[0]["nullable"] == 1
    assert planned[0]["auto_learned"] == 1


def test_an_already_declared_field_is_left_alone():
    """An authored label must not be re-guessed on the next uplink."""
    assert plan_learned_fields(
        {"temperature": 21.5}, declared_keys={"temperature"}
    ) == []


def test_declared_comparison_ignores_case_and_padding():
    assert plan_learned_fields(
        {"humidity": 50.0}, declared_keys={"  Humidity "}
    ) == []


@pytest.mark.parametrize(
    "key, reason",
    [
        ("temperature_1", "raw Cayenne channel duplicates its canonical key"),
        ("relative_humidity_12", "same, with a multi-digit channel"),
        ("rssi", "platform field, not a reading"),
        ("profile_code", "platform field"),
        ("alarm_active", "platform field"),
        ("Recognized Text", "spaces are not identifier-like"),
        ("9lives", "must not start with a digit"),
        ("x", "too short to be meaningful"),
        ("a" * 41, "beyond the length bound"),
        ("drop table devices", "not identifier-like"),
        ("field-with-dash", "not identifier-like"),
    ],
)
def test_keys_that_must_not_be_learned(key, reason):
    assert plan_learned_fields({key: 1}, declared_keys=set()) == [], reason


@pytest.mark.parametrize(
    "value, expected",
    [(True, "boolean"), (False, "boolean"), (1, "number"), (1.5, "number"),
     ("text", "string")],
)
def test_data_types_are_inferred_from_the_value(value, expected):
    assert infer_data_type(value) == expected


@pytest.mark.parametrize("value", [None, {"a": 1}, [1, 2], object()])
def test_values_with_no_sensible_type_are_skipped(value):
    assert infer_data_type(value) is None
    assert plan_learned_fields({"some_field": value}, declared_keys=set()) == []


def test_booleans_are_not_mistaken_for_numbers():
    """bool is a subclass of int in Python, so order of checks matters."""
    planned = plan_learned_fields({"cam_locked": True}, declared_keys=set())
    assert planned[0]["data_type"] == "boolean"


def test_the_cap_bounds_what_one_profile_can_learn():
    """A device sending nonsense must not be able to grow a profile forever."""
    telemetry = {f"metric_{chr(97 + i // 26)}{chr(97 + i % 26)}": i
                 for i in range(MAX_LEARNED_FIELDS + 20)}
    planned = plan_learned_fields(telemetry, declared_keys=set())
    assert len(planned) == MAX_LEARNED_FIELDS


def test_the_cap_accounts_for_what_was_already_learned():
    telemetry = {f"metric_{chr(97 + i)}x": i for i in range(10)}
    planned = plan_learned_fields(
        telemetry, declared_keys=set(), already_learned=MAX_LEARNED_FIELDS - 3
    )
    assert len(planned) == 3


def test_a_full_profile_learns_nothing_more():
    assert plan_learned_fields(
        {"another_field": 1}, declared_keys=set(),
        already_learned=MAX_LEARNED_FIELDS,
    ) == []


def test_planning_is_stable_across_identical_uplinks():
    telemetry = {"zebra_count": 1, "alpha_reading": 2, "middle_value": 3}
    first = plan_learned_fields(telemetry, declared_keys=set())
    second = plan_learned_fields(telemetry, declared_keys=set())
    assert first == second
    assert [f["field_key"] for f in first] == [
        "alpha_reading", "middle_value", "zebra_count",
    ]


@pytest.mark.parametrize(
    "key, label",
    [
        ("recognized_text", "Recognized Text"),
        ("cam_locked", "CAM Locked"),
        ("co2_level", "CO2 Level"),
        ("humidity", "Humidity"),
    ],
)
def test_labels_are_a_readable_first_guess(key, label):
    assert humanize(key) == label


def test_any_channel_shaped_key_is_refused_even_without_a_twin():
    """
    Keys ending in _<digits> are raw Cayenne channels by convention here, and
    every one of them sits beside a canonical key carrying the same value. A
    device using ten channels would otherwise learn ten duplicate fields and
    exhaust the cap with noise, so the shape is refused outright rather than
    only when its canonical twin happens to be in the same uplink.

    The cost is that a custom formatter emitting sensor_1 will not be learned;
    naming it sensor_one is enough.
    """
    assert plan_learned_fields({"sensor_1": 5}, declared_keys=set()) == []
    assert plan_learned_fields({"sensor_one": 5}, declared_keys=set()) != []
