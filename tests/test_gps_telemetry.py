import json

import pytest

from app.main import (
    get_sensor_profile_detail,
    process_ttn_webhook_background,
    validate_decoded_payload_against_profile,
)
from app.services.profile_learning import plan_learned_fields


@pytest.mark.parametrize(
    "coordinates",
    [
        {"latitude": 30.0444, "longitude": 31.2357, "altitude": 23.5},
        {"latitude": -33.8688, "longitude": -151.2093, "altitude": -12.25},
        {"latitude": 0, "longitude": 0, "altitude": 0},
    ],
)
def test_cayenne_gps_coordinates_are_stored_and_learnable(coordinates):
    payload = {"gps_1": coordinates, "temperature_2": 24.5}
    result = validate_decoded_payload_against_profile(
        {"profile_code": "CAYENNE_LPP_V1"}, payload,
    )
    assert result["valid"]
    assert not result["unknown_fields"]
    telemetry = result["telemetry"]
    for key, value in coordinates.items():
        assert telemetry[key] == value
        assert telemetry[f"{key}_1"] == value
    assert telemetry["temperature"] == 24.5
    assert {f["field_key"] for f in plan_learned_fields(telemetry, set())} == {
        "latitude", "longitude", "altitude", "temperature",
    }
    assert payload["gps_1"] == coordinates  # Do not mutate the webhook input.


@pytest.mark.parametrize(
    "bad_coordinates",
    [
        {"latitude": 91, "longitude": 31, "altitude": 20},
        {"latitude": 30, "longitude": -181, "altitude": 20},
        {"latitude": 30, "longitude": 31, "altitude": 83887},
        {"latitude": True, "longitude": 31, "altitude": 20},
        {"latitude": "30", "longitude": 31, "altitude": 20},
        {"latitude": float("nan"), "longitude": 31, "altitude": 20},
        {"latitude": 30, "longitude": float("inf"), "altitude": 20},
        {"latitude": 30, "longitude": 31},
        {"latitude": {"value": 30}, "longitude": 31, "altitude": 20},
    ],
)
def test_bad_gps_objects_do_not_create_partial_coordinates(bad_coordinates):
    result = validate_decoded_payload_against_profile(
        {"profile_code": "CAYENNE_LPP_V1"},
        {"gps_1": bad_coordinates, "other_object": {"latitude": 30}},
    )
    assert result["telemetry"] == {}
    assert "other_object" in result["unknown_fields"]


def test_multiple_gps_channels_and_explicit_scalars_keep_their_values():
    result = validate_decoded_payload_against_profile(
        {"profile_code": "CAYENNE_LPP_V1"},
        {
            "gps_1": {"latitude": 30, "longitude": 31, "altitude": 20},
            "gps_2": {"latitude": -30, "longitude": -31, "altitude": -20},
            "latitude": 10,
        },
    )
    telemetry = result["telemetry"]
    assert telemetry["latitude"] == 10
    assert telemetry["latitude_1"] == 30
    assert telemetry["latitude_2"] == -30
    assert telemetry["longitude_2"] == -31
    assert telemetry["altitude_2"] == -20


def test_gps_uplink_survives_discovery_storage_and_profile_learning(db_conn):
    device_id = "gps-discovery-test"
    result = process_ttn_webhook_background({
        "end_device_ids": {
            "device_id": device_id,
            "dev_eui": "0004A30B001C0599",
            "application_ids": {"application_id": "smart-building-environment"},
        },
        "uplink_message": {
            "f_port": 1,
            "decoded_payload": {
                "gps_1": {"latitude": 30.0444, "longitude": 31.2357, "altitude": 23.5},
            },
        },
    })
    assert result["status"] in {
        "ok_profile_validated", "ok_local_test_profile_validated",
        "ok_profile_validated_thingsboard_failed",
    }
    row = db_conn.execute(
        "SELECT * FROM devices WHERE device_id = ?", (device_id,),
    ).fetchone()
    assert row is not None
    assert row["is_placed"] == 0
    stored = db_conn.execute(
        "SELECT telemetry FROM device_latest_telemetry WHERE device_id = ?",
        (device_id,),
    ).fetchone()
    telemetry = json.loads(stored["telemetry"])
    assert telemetry["latitude"] == 30.0444
    assert telemetry["longitude"] == 31.2357
    assert telemetry["altitude"] == 23.5
    assert telemetry["latitude_1"] == 30.0444
    profile = get_sensor_profile_detail(db_conn, "CAYENNE_LPP_V1", include_formatter=False)
    keys = {field["field_key"] for field in profile["fields"]}
    assert {"latitude", "longitude", "altitude"} <= keys
