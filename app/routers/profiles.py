import logging

logger = logging.getLogger(__name__)

import sqlite3

from fastapi import APIRouter, HTTPException, Request

from app.db.connection import get_db_connection as db
from app.main import (
    clone_sensor_profile_record,
    create_sensor_profile_record,
    delete_sensor_profile_record,
    generate_unique_sensor_profile_code,
    get_sensor_catalog_detail,
    get_sensor_profile_detail,
    list_sensor_profile_summaries,
    log_audit_event,
    normalize_sensor_catalog_definition,
    profile_utc_now_iso,
    profile_value_to_boolean,
    sensor_profile_actor_from_request,
    set_sensor_catalog_enabled_api,
    set_sensor_profile_enabled,
    update_sensor_profile_record,
    validate_sensor_profile_definition,
)

router = APIRouter()

@router.get("/sensor-profiles")
def sensor_profiles_api(
    include_disabled: bool = True,
):
    """
    Return all sensor-profile summaries.

    The full TTN formatter is intentionally excluded from this
    lightweight list endpoint.
    """

    conn = db()

    try:
        profiles = list_sensor_profile_summaries(
            conn,
            include_disabled=include_disabled,
        )

        return {
            "status": "ok",
            "count": len(profiles),
            "profiles": profiles,
        }

    finally:
        conn.close()
@router.get("/sensor-profiles/system/status")
def sensor_profile_system_status_api():
    """
    Return database counts for the profile-driven architecture.
    """

    conn = db()

    try:
        profile_count = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM sensor_profiles
            """
        ).fetchone()["total"]

        active_profile_count = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM sensor_profiles
            WHERE enabled = 1
              AND status = 'active'
            """
        ).fetchone()["total"]

        sensor_count = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM sensor_catalog
            """
        ).fetchone()["total"]

        firmware_module_count = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM firmware_modules
            """
        ).fetchone()["total"]

        telemetry_field_count = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM sensor_profile_fields
            """
        ).fetchone()["total"]

        alarm_rule_count = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM sensor_profile_rules
            """
        ).fetchone()["total"]

        assigned_device_count = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM devices
            WHERE profile_id IS NOT NULL
               OR profile_code IS NOT NULL
            """
        ).fetchone()["total"]

        configuration_history_count = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM device_configuration_history
            """
        ).fetchone()["total"]

        return {
            "status": "ok",
            "profile_count": profile_count,
            "active_profile_count": active_profile_count,
            "sensor_count": sensor_count,
            "firmware_module_count": firmware_module_count,
            "telemetry_field_count": telemetry_field_count,
            "alarm_rule_count": alarm_rule_count,
            "assigned_device_count": assigned_device_count,
            "configuration_history_count": (
                configuration_history_count
            ),
        }

    finally:
        conn.close()
@router.post("/sensor-profiles/validate")
def validate_sensor_profile_api(
    profile_definition: dict,
):
    """
    Validate a profile definition without saving it.
    """

    conn = db()

    try:
        profile_id = profile_definition.get("id")

        if profile_id is not None:
            try:
                profile_id = int(profile_id)
            except Exception:
                raise HTTPException(
                    status_code=400,
                    detail="Profile id must be an integer",
                )

        result = validate_sensor_profile_definition(
            conn,
            profile_definition,
            exclude_profile_id=profile_id,
        )

        return {
            "status": (
                "valid"
                if result["valid"]
                else "invalid"
            ),
            "valid": result["valid"],
            "errors": result["errors"],
            "schema_checksum": result[
                "schema_checksum"
            ],
            "normalized": result["normalized"],
        }

    finally:
        conn.close()
@router.get("/sensor-profiles/by-code/{profile_code}")
def sensor_profile_detail_by_code_api(
    profile_code: str,
    include_formatter: bool = False,
):
    """
    Return one complete profile using its profile code.
    """

    conn = db()

    try:
        profile = get_sensor_profile_detail(
            conn,
            profile_code,
            include_formatter=include_formatter,
        )

        if not profile:
            raise HTTPException(
                status_code=404,
                detail=(
                    "Sensor profile not found: "
                    + profile_code
                ),
            )

        return {
            "status": "ok",
            "profile": profile,
        }

    finally:
        conn.close()
@router.get("/sensor-profiles/{profile_id}")
def sensor_profile_detail_by_id_api(
    profile_id: int,
    include_formatter: bool = False,
):
    """
    Return one complete profile using its numeric database ID.
    """

    conn = db()

    try:
        profile = get_sensor_profile_detail(
            conn,
            profile_id,
            include_formatter=include_formatter,
        )

        if not profile:
            raise HTTPException(
                status_code=404,
                detail=(
                    "Sensor profile not found with id "
                    + str(profile_id)
                ),
            )

        return {
            "status": "ok",
            "profile": profile,
        }

    finally:
        conn.close()
@router.get("/sensor-catalog")
def sensor_catalog_api(
    include_disabled: bool = True,
):
    """
    Return physical and compatibility sensors registered in
    the sensor catalog.
    """

    conn = db()

    try:
        sql = """
            SELECT
                catalog.id,
                catalog.sensor_code,
                catalog.manufacturer,
                catalog.model,
                catalog.use_case,
                catalog.protocol,
                catalog.default_bus,
                catalog.default_address,
                catalog.datasheet_url,
                catalog.description,
                catalog.enabled,
                catalog.created_at,
                catalog.updated_at,

                COUNT(
                    DISTINCT relation.profile_id
                ) AS profile_count

            FROM sensor_catalog catalog

            LEFT JOIN sensor_profile_sensors relation
                ON relation.sensor_id = catalog.id
        """

        parameters = []

        if not include_disabled:
            sql += """
                WHERE catalog.enabled = 1
            """

        sql += """
            GROUP BY catalog.id

            ORDER BY
                catalog.use_case,
                catalog.manufacturer,
                catalog.model
        """

        rows = conn.execute(
            sql,
            parameters,
        ).fetchall()

        sensors = []

        for row in rows:
            sensor = dict(row)
            sensor["enabled"] = bool(
                sensor.get("enabled")
            )
            sensors.append(sensor)

        return {
            "status": "ok",
            "count": len(sensors),
            "sensors": sensors,
        }

    finally:
        conn.close()
@router.get("/sensor-catalog/{sensor_id}")
def sensor_catalog_detail_api(sensor_id: int):
    """Return one physical sensor catalog record."""

    conn = db()

    try:
        sensor = get_sensor_catalog_detail(
            conn,
            sensor_id,
        )

        if not sensor:
            raise HTTPException(
                status_code=404,
                detail="Sensor catalog record not found",
            )

        return {
            "status": "ok",
            "sensor": sensor,
        }

    finally:
        conn.close()
@router.post(
    "/sensor-catalog",
    status_code=201,
)
def create_sensor_catalog_api(
    request: Request,
    sensor_definition: dict,
):
    """Create a physical sensor catalog record from Admin Portal."""

    conn = db()

    try:
        actor = sensor_profile_actor_from_request(request)

        normalized = normalize_sensor_catalog_definition(
            conn,
            sensor_definition,
        )

        now = profile_utc_now_iso()

        cursor = conn.execute(
            """
            INSERT INTO sensor_catalog(
                sensor_code,
                manufacturer,
                model,
                use_case,
                protocol,
                default_bus,
                default_address,
                datasheet_url,
                description,
                enabled,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                normalized["sensor_code"],
                normalized["manufacturer"],
                normalized["model"],
                normalized["use_case"],
                normalized["protocol"],
                normalized["default_bus"],
                normalized["default_address"],
                normalized["datasheet_url"],
                normalized["description"],
                int(normalized["enabled"]),
                now,
                now,
            ),
        )

        sensor_id = int(cursor.lastrowid)
        conn.commit()

        sensor = get_sensor_catalog_detail(
            conn,
            sensor_id,
        )

        log_audit_event(
            conn,
            action="create_sensor_catalog",
            actor=actor,
            target_type="sensor_catalog",
            target_id=sensor_id,
            details={"sensor": sensor},
            message=(
                "Sensor catalog record created: "
                + normalized["sensor_code"]
            ),
        )

        return {
            "status": "created",
            "message": "Sensor catalog record created",
            "sensor": sensor,
        }

    except ValueError as error:
        conn.rollback()
        raise HTTPException(
            status_code=400,
            detail=str(error),
        )

    except sqlite3.IntegrityError as error:
        conn.rollback()
        raise HTTPException(
            status_code=409,
            detail=(
                "The sensor code already exists or conflicts with "
                "another catalog record: "
                + str(error)
            ),
        )

    except HTTPException:
        conn.rollback()
        raise

    except Exception as error:
        conn.rollback()
        logger.error("Create sensor catalog error: %s", error)
        raise HTTPException(
            status_code=500,
            detail="Could not create the sensor catalog record",
        )

    finally:
        conn.close()
@router.put("/sensor-catalog/{sensor_id}")
def update_sensor_catalog_api(
    sensor_id: int,
    request: Request,
    sensor_definition: dict,
):
    """Update one physical sensor catalog record."""

    conn = db()

    try:
        existing = get_sensor_catalog_detail(
            conn,
            sensor_id,
        )

        if not existing:
            raise HTTPException(
                status_code=404,
                detail="Sensor catalog record not found",
            )

        actor = sensor_profile_actor_from_request(request)

        normalized = normalize_sensor_catalog_definition(
            conn,
            sensor_definition,
            exclude_sensor_id=sensor_id,
        )

        conn.execute(
            """
            UPDATE sensor_catalog
            SET
                sensor_code = ?,
                manufacturer = ?,
                model = ?,
                use_case = ?,
                protocol = ?,
                default_bus = ?,
                default_address = ?,
                datasheet_url = ?,
                description = ?,
                enabled = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                normalized["sensor_code"],
                normalized["manufacturer"],
                normalized["model"],
                normalized["use_case"],
                normalized["protocol"],
                normalized["default_bus"],
                normalized["default_address"],
                normalized["datasheet_url"],
                normalized["description"],
                int(normalized["enabled"]),
                profile_utc_now_iso(),
                sensor_id,
            ),
        )

        conn.commit()

        sensor = get_sensor_catalog_detail(
            conn,
            sensor_id,
        )

        log_audit_event(
            conn,
            action="update_sensor_catalog",
            actor=actor,
            target_type="sensor_catalog",
            target_id=sensor_id,
            details={
                "old": existing,
                "new": sensor,
            },
            message=(
                "Sensor catalog record updated: "
                + normalized["sensor_code"]
            ),
        )

        return {
            "status": "updated",
            "message": "Sensor catalog record updated",
            "sensor": sensor,
        }

    except ValueError as error:
        conn.rollback()
        raise HTTPException(
            status_code=400,
            detail=str(error),
        )

    except sqlite3.IntegrityError as error:
        conn.rollback()
        raise HTTPException(
            status_code=409,
            detail=(
                "The sensor update conflicts with another record: "
                + str(error)
            ),
        )

    except HTTPException:
        conn.rollback()
        raise

    except Exception as error:
        conn.rollback()
        logger.error("Update sensor catalog error: %s", error)
        raise HTTPException(
            status_code=500,
            detail="Could not update the sensor catalog record",
        )

    finally:
        conn.close()
@router.post("/sensor-catalog/{sensor_id}/enable")
def enable_sensor_catalog_api(
    sensor_id: int,
    request: Request,
):
    return set_sensor_catalog_enabled_api(
        sensor_id,
        request,
        True,
    )
@router.post("/sensor-catalog/{sensor_id}/disable")
def disable_sensor_catalog_api(
    sensor_id: int,
    request: Request,
):
    return set_sensor_catalog_enabled_api(
        sensor_id,
        request,
        False,
    )
@router.delete("/sensor-catalog/{sensor_id}")
def delete_sensor_catalog_api(
    sensor_id: int,
    request: Request,
):
    """Delete an unused physical sensor catalog record."""

    conn = db()

    try:
        sensor = get_sensor_catalog_detail(
            conn,
            sensor_id,
        )

        if not sensor:
            raise HTTPException(
                status_code=404,
                detail="Sensor catalog record not found",
            )

        if not sensor.get("can_delete"):
            raise HTTPException(
                status_code=409,
                detail=(
                    "This sensor is used by one or more profiles. "
                    "Disable it instead of deleting it."
                ),
            )

        actor = sensor_profile_actor_from_request(request)

        conn.execute(
            "DELETE FROM sensor_catalog WHERE id = ?",
            (sensor_id,),
        )

        conn.commit()

        log_audit_event(
            conn,
            action="delete_sensor_catalog",
            actor=actor,
            target_type="sensor_catalog",
            target_id=sensor_id,
            details={"deleted_sensor": sensor},
            message=(
                "Sensor catalog record deleted: "
                + str(sensor.get("sensor_code"))
            ),
        )

        return {
            "status": "deleted",
            "message": "Sensor catalog record deleted",
            "sensor": sensor,
        }

    except HTTPException:
        conn.rollback()
        raise

    except sqlite3.IntegrityError as error:
        conn.rollback()
        raise HTTPException(
            status_code=409,
            detail=(
                "The sensor is still referenced by another record: "
                + str(error)
            ),
        )

    except Exception as error:
        conn.rollback()
        logger.error("Delete sensor catalog error: %s", error)
        raise HTTPException(
            status_code=500,
            detail="Could not delete the sensor catalog record",
        )

    finally:
        conn.close()
@router.post(
    "/sensor-profiles",
    status_code=201,
)
def create_sensor_profile_api(
    request: Request,
    profile_definition: dict,
):
    """
    Create a new editable sensor profile.
    """

    conn = db()

    try:
        actor = sensor_profile_actor_from_request(
            request
        )

        definition = dict(
            profile_definition or {}
        )

        change_note = str(
            definition.pop(
                "change_note",
                "Profile created through API",
            )
        ).strip()

        created_profile = create_sensor_profile_record(
            conn,
            definition,
            actor=actor,
            change_note=change_note,
        )

        conn.commit()

        return {
            "status": "created",
            "message": "Sensor profile created",
            "profile": created_profile,
        }

    except ValueError as error:
        conn.rollback()

        raise HTTPException(
            status_code=400,
            detail=str(error),
        )

    except sqlite3.IntegrityError as error:
        conn.rollback()

        raise HTTPException(
            status_code=409,
            detail=(
                "The sensor profile conflicts with "
                "an existing database record: "
                + str(error)
            ),
        )

    except HTTPException:
        conn.rollback()
        raise

    except Exception as error:
        conn.rollback()

        logger.info(
            "Create sensor profile API error: %s",
            error,
        )

        raise HTTPException(
            status_code=500,
            detail=(
                "Could not create the sensor profile"
            ),
        )

    finally:
        conn.close()
@router.put("/sensor-profiles/{profile_id}")
def update_sensor_profile_api(
    profile_id: int,
    request: Request,
    profile_definition: dict,
):
    """
    Update a custom profile and create a new version.

    Seeded system profiles must be cloned before editing.
    """

    conn = db()

    try:
        actor = sensor_profile_actor_from_request(
            request
        )

        definition = dict(
            profile_definition or {}
        )

        change_note = str(
            definition.pop(
                "change_note",
                "Profile updated through API",
            )
        ).strip()

        updated_profile = update_sensor_profile_record(
            conn,
            profile_id,
            definition,
            actor=actor,
            change_note=change_note,
        )

        conn.commit()

        return {
            "status": "updated",
            "message": "Sensor profile updated",
            "profile": updated_profile,
        }

    except ValueError as error:
        conn.rollback()

        error_message = str(error)

        status_code = 404

        if "not found" not in error_message.lower():
            status_code = 400

        raise HTTPException(
            status_code=status_code,
            detail=error_message,
        )

    except sqlite3.IntegrityError as error:
        conn.rollback()

        raise HTTPException(
            status_code=409,
            detail=(
                "The sensor profile update conflicts "
                "with an existing database record: "
                + str(error)
            ),
        )

    except HTTPException:
        conn.rollback()
        raise

    except Exception as error:
        conn.rollback()

        logger.info(
            "Update sensor profile API error: %s",
            error,
        )

        raise HTTPException(
            status_code=500,
            detail=(
                "Could not update the sensor profile"
            ),
        )

    finally:
        conn.close()
@router.post(
    "/sensor-profiles/{profile_id}/clone",
    status_code=201,
)
def clone_sensor_profile_api(
    profile_id: int,
    request: Request,
    clone_request: dict,
):
    """
    Clone a profile using either the advanced clone workflow
    or the simplified administrator workflow.
    """

    conn = db()

    try:
        actor = sensor_profile_actor_from_request(
            request
        )

        new_profile_name = str(
            clone_request.get(
                "new_profile_name",
                "",
            )
        ).strip()

        if not new_profile_name:
            raise ValueError(
                "new_profile_name is required"
            )

        new_profile_code = str(
            clone_request.get(
                "new_profile_code",
                "",
            )
        ).strip().upper()

        if not new_profile_code:
            new_profile_code = (
                generate_unique_sensor_profile_code(
                    conn,
                    new_profile_name,
                )
            )

        activate = profile_value_to_boolean(
            clone_request.get(
                "activate",
                False,
            ),
            default=False,
        )

        uplink_interval_seconds = (
            clone_request.get(
                "uplink_interval_seconds"
            )
        )

        alarm_thresholds = (
            clone_request.get(
                "alarm_thresholds",
                {},
            )
        )

        cloned_profile = (
            clone_sensor_profile_record(
                conn,
                profile_id,
                new_profile_code,
                new_profile_name,
                actor=actor,
                activate=activate,
                uplink_interval_seconds=(
                    uplink_interval_seconds
                ),
                alarm_thresholds=(
                    alarm_thresholds
                ),
            )
        )

        conn.commit()

        return {
            "status": (
                "created"
                if activate
                else "cloned"
            ),
            "message": (
                "Sensor profile created and activated"
                if activate
                else "Sensor profile cloned"
            ),
            "source_profile_id": profile_id,
            "generated_profile_code": (
                new_profile_code
            ),
            "profile": cloned_profile,
        }

    except ValueError as error:
        conn.rollback()

        error_message = str(error)
        lower_error = error_message.lower()

        if "not found" in lower_error:
            status_code = 404

        elif (
            "already exists" in lower_error
            or "conflicts" in lower_error
        ):
            status_code = 409

        else:
            status_code = 400

        raise HTTPException(
            status_code=status_code,
            detail=error_message,
        )

    except sqlite3.IntegrityError as error:
        conn.rollback()

        raise HTTPException(
            status_code=409,
            detail=(
                "The requested profile code already "
                "exists or conflicts with another record: "
                + str(error)
            ),
        )

    except HTTPException:
        conn.rollback()
        raise

    except Exception as error:
        conn.rollback()

        logger.info(
            "Clone sensor profile API error: %s",
            error,
        )

        raise HTTPException(
            status_code=500,
            detail=(
                "Could not clone the sensor profile"
            ),
        )

    finally:
        conn.close()
@router.post("/sensor-profiles/{profile_id}/enable")
def enable_sensor_profile_api(
    profile_id: int,
    request: Request,
):
    """
    Enable a sensor profile.
    """

    conn = db()

    try:
        actor = sensor_profile_actor_from_request(
            request
        )

        profile = set_sensor_profile_enabled(
            conn,
            profile_id,
            True,
            actor=actor,
        )

        conn.commit()

        return {
            "status": "enabled",
            "message": "Sensor profile enabled",
            "profile": profile,
        }

    except ValueError as error:
        conn.rollback()

        raise HTTPException(
            status_code=404,
            detail=str(error),
        )

    except HTTPException:
        conn.rollback()
        raise

    except Exception as error:
        conn.rollback()

        logger.info(
            "Enable sensor profile API error: %s",
            error,
        )

        raise HTTPException(
            status_code=500,
            detail=(
                "Could not enable the sensor profile"
            ),
        )

    finally:
        conn.close()
@router.post("/sensor-profiles/{profile_id}/disable")
def disable_sensor_profile_api(
    profile_id: int,
    request: Request,
):
    """
    Disable a sensor profile.

    Disabled profiles remain stored for existing devices and
    history but will later be excluded from new provisioning.
    """

    conn = db()

    try:
        actor = sensor_profile_actor_from_request(
            request
        )

        profile = set_sensor_profile_enabled(
            conn,
            profile_id,
            False,
            actor=actor,
        )

        conn.commit()

        return {
            "status": "disabled",
            "message": "Sensor profile disabled",
            "profile": profile,
        }

    except ValueError as error:
        conn.rollback()

        raise HTTPException(
            status_code=404,
            detail=str(error),
        )

    except HTTPException:
        conn.rollback()
        raise

    except Exception as error:
        conn.rollback()

        logger.info(
            "Disable sensor profile API error: %s",
            error,
        )

        raise HTTPException(
            status_code=500,
            detail=(
                "Could not disable the sensor profile"
            ),
        )

    finally:
        conn.close()
@router.delete("/sensor-profiles/{profile_id}")
def delete_sensor_profile_api(
    profile_id: int,
    request: Request,
):
    """
    Delete an unused custom profile.

    System profiles and profiles referenced by devices or
    configuration history cannot be deleted.
    """

    conn = db()

    try:
        # Resolve the user now so authentication information
        # remains available for later audit integration.
        sensor_profile_actor_from_request(request)

        deleted_profile = delete_sensor_profile_record(
            conn,
            profile_id,
        )

        conn.commit()

        return {
            "status": "deleted",
            "message": "Sensor profile deleted",
            "profile": deleted_profile,
        }

    except ValueError as error:
        conn.rollback()

        error_message = str(error)
        lower_error = error_message.lower()

        if "not found" in lower_error:
            status_code = 404

        elif (
            "cannot be deleted" in lower_error
            or "assigned" in lower_error
            or "history" in lower_error
        ):
            status_code = 409

        else:
            status_code = 400

        raise HTTPException(
            status_code=status_code,
            detail=error_message,
        )

    except sqlite3.IntegrityError as error:
        conn.rollback()

        raise HTTPException(
            status_code=409,
            detail=(
                "The profile is still referenced by "
                "another database record: "
                + str(error)
            ),
        )

    except HTTPException:
        conn.rollback()
        raise

    except Exception as error:
        conn.rollback()

        logger.info(
            "Delete sensor profile API error: %s",
            error,
        )

        raise HTTPException(
            status_code=500,
            detail=(
                "Could not delete the sensor profile"
            ),
        )

    finally:
        conn.close()