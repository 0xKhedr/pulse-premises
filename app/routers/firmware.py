import logging

logger = logging.getLogger(__name__)

import io
import re
import sqlite3
import zipfile

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.db.connection import get_db_connection as db
from app.main import (
    get_firmware_module_detail,
    log_audit_event,
    normalize_firmware_module_definition,
    profile_utc_now_iso,
    sensor_profile_actor_from_request,
    set_firmware_module_enabled_api,
)

router = APIRouter()

@router.get("/firmware-modules")
def firmware_modules_api(
    include_disabled: bool = True,
):
    """
    Return the firmware drivers/modules supported by profiles.
    """

    conn = db()

    try:
        sql = """
            SELECT
                module.id,
                module.module_key,
                module.display_name,
                module.driver_class,
                module.protocol,
                module.library_name,
                module.library_version,
                module.supported_board,
                module.min_firmware_version,
                module.source_file,
                module.notes,
                module.enabled,
                module.created_at,
                module.updated_at,

                COUNT(
                    DISTINCT relation.profile_id
                ) AS profile_count

            FROM firmware_modules module

            LEFT JOIN sensor_profile_sensors relation
                ON relation.firmware_module_id = module.id
        """

        parameters = []

        if not include_disabled:
            sql += """
                WHERE module.enabled = 1
            """

        sql += """
            GROUP BY module.id

            ORDER BY module.display_name
        """

        rows = conn.execute(
            sql,
            parameters,
        ).fetchall()

        modules = []

        for row in rows:
            module = dict(row)
            module["enabled"] = bool(
                module.get("enabled")
            )

            profile_rows = conn.execute(
                """
                SELECT DISTINCT profile_id
                FROM (
                    SELECT profile_id
                    FROM sensor_profile_sensors
                    WHERE firmware_module_id = ?

                    UNION

                    SELECT profile_id
                    FROM profile_firmware_compatibility
                    WHERE firmware_module_id = ?
                )
                """,
                (module["id"], module["id"]),
            ).fetchall()

            module["profile_count"] = len(profile_rows)
            module["can_delete"] = (
                module["profile_count"] == 0
            )

            modules.append(module)

        return {
            "status": "ok",
            "count": len(modules),
            "modules": modules,
        }

    finally:
        conn.close()
@router.get("/firmware-modules/{module_id}")
def firmware_module_detail_api(module_id: int):
    """Return one registered firmware driver/module."""

    conn = db()

    try:
        module = get_firmware_module_detail(
            conn,
            module_id,
        )

        if not module:
            raise HTTPException(
                status_code=404,
                detail="Firmware module not found",
            )

        return {
            "status": "ok",
            "module": module,
        }

    finally:
        conn.close()
@router.post(
    "/firmware-modules",
    status_code=201,
)
def create_firmware_module_api(
    request: Request,
    module_definition: dict,
):
    """Register a driver already available in a firmware build."""

    conn = db()

    try:
        actor = sensor_profile_actor_from_request(request)

        normalized = normalize_firmware_module_definition(
            conn,
            module_definition,
        )

        now = profile_utc_now_iso()

        cursor = conn.execute(
            """
            INSERT INTO firmware_modules(
                module_key,
                display_name,
                driver_class,
                protocol,
                library_name,
                library_version,
                supported_board,
                min_firmware_version,
                source_file,
                notes,
                enabled,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                normalized["module_key"],
                normalized["display_name"],
                normalized["driver_class"],
                normalized["protocol"],
                normalized["library_name"],
                normalized["library_version"],
                normalized["supported_board"],
                normalized["min_firmware_version"],
                normalized["source_file"],
                normalized["notes"],
                int(normalized["enabled"]),
                now,
                now,
            ),
        )

        module_id = int(cursor.lastrowid)
        conn.commit()

        module = get_firmware_module_detail(
            conn,
            module_id,
        )

        log_audit_event(
            conn,
            action="create_firmware_module",
            actor=actor,
            target_type="firmware_module",
            target_id=module_id,
            details={"module": module},
            message=(
                "Firmware module registered: "
                + normalized["module_key"]
            ),
        )

        return {
            "status": "created",
            "message": "Firmware module registered",
            "module": module,
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
                "The firmware module conflicts with an existing record: "
                + str(error)
            ),
        )

    except HTTPException:
        conn.rollback()
        raise

    except Exception as error:
        conn.rollback()
        logger.error("Create firmware module error: %s", error)
        raise HTTPException(
            status_code=500,
            detail="Could not register the firmware module",
        )

    finally:
        conn.close()
@router.put("/firmware-modules/{module_id}")
def update_firmware_module_api(
    module_id: int,
    request: Request,
    module_definition: dict,
):
    """Update one registered firmware module."""

    conn = db()

    try:
        existing = get_firmware_module_detail(
            conn,
            module_id,
        )

        if not existing:
            raise HTTPException(
                status_code=404,
                detail="Firmware module not found",
            )

        actor = sensor_profile_actor_from_request(request)

        normalized = normalize_firmware_module_definition(
            conn,
            module_definition,
            exclude_module_id=module_id,
        )

        conn.execute(
            """
            UPDATE firmware_modules
            SET
                module_key = ?,
                display_name = ?,
                driver_class = ?,
                protocol = ?,
                library_name = ?,
                library_version = ?,
                supported_board = ?,
                min_firmware_version = ?,
                source_file = ?,
                notes = ?,
                enabled = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                normalized["module_key"],
                normalized["display_name"],
                normalized["driver_class"],
                normalized["protocol"],
                normalized["library_name"],
                normalized["library_version"],
                normalized["supported_board"],
                normalized["min_firmware_version"],
                normalized["source_file"],
                normalized["notes"],
                int(normalized["enabled"]),
                profile_utc_now_iso(),
                module_id,
            ),
        )

        conn.commit()

        module = get_firmware_module_detail(
            conn,
            module_id,
        )

        log_audit_event(
            conn,
            action="update_firmware_module",
            actor=actor,
            target_type="firmware_module",
            target_id=module_id,
            details={
                "old": existing,
                "new": module,
            },
            message=(
                "Firmware module updated: "
                + normalized["module_key"]
            ),
        )

        return {
            "status": "updated",
            "message": "Firmware module updated",
            "module": module,
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
                "The firmware-module update conflicts with another record: "
                + str(error)
            ),
        )

    except HTTPException:
        conn.rollback()
        raise

    except Exception as error:
        conn.rollback()
        logger.error("Update firmware module error: %s", error)
        raise HTTPException(
            status_code=500,
            detail="Could not update the firmware module",
        )

    finally:
        conn.close()
@router.post("/firmware-modules/{module_id}/enable")
def enable_firmware_module_api(
    module_id: int,
    request: Request,
):
    return set_firmware_module_enabled_api(
        module_id,
        request,
        True,
    )
@router.post("/firmware-modules/{module_id}/disable")
def disable_firmware_module_api(
    module_id: int,
    request: Request,
):
    return set_firmware_module_enabled_api(
        module_id,
        request,
        False,
    )
@router.delete("/firmware-modules/{module_id}")
def delete_firmware_module_api(
    module_id: int,
    request: Request,
):
    """Delete a firmware module only when no profile references it."""

    conn = db()

    try:
        module = get_firmware_module_detail(
            conn,
            module_id,
        )

        if not module:
            raise HTTPException(
                status_code=404,
                detail="Firmware module not found",
            )

        if not module.get("can_delete"):
            raise HTTPException(
                status_code=409,
                detail=(
                    "This firmware module is used by one or more profiles. "
                    "Disable it instead of deleting it."
                ),
            )

        actor = sensor_profile_actor_from_request(request)

        conn.execute(
            "DELETE FROM firmware_modules WHERE id = ?",
            (module_id,),
        )

        conn.commit()

        log_audit_event(
            conn,
            action="delete_firmware_module",
            actor=actor,
            target_type="firmware_module",
            target_id=module_id,
            details={"deleted_module": module},
            message=(
                "Firmware module deleted: "
                + str(module.get("module_key"))
            ),
        )

        return {
            "status": "deleted",
            "message": "Firmware module deleted",
            "module": module,
        }

    except HTTPException:
        conn.rollback()
        raise

    except sqlite3.IntegrityError as error:
        conn.rollback()
        raise HTTPException(
            status_code=409,
            detail=(
                "The firmware module is still referenced: "
                + str(error)
            ),
        )

    except Exception as error:
        conn.rollback()
        logger.error("Delete firmware module error: %s", error)
        raise HTTPException(
            status_code=500,
            detail="Could not delete the firmware module",
        )

    finally:
        conn.close()
@router.get("/api/firmware/generate-sensor-template")
def generate_sensor_template(name: str):
    if not name or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}', name):
        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid sensor name. Use a C identifier: start with a letter or "
                "underscore, followed by letters, digits, or underscores (max 64 chars)."
            ),
        )

    h_content = f'''#ifndef {name.upper()}_SENSOR_MODULE_H
#define {name.upper()}_SENSOR_MODULE_H

#include "SensorModule.h"

class {name}SensorModule : public SensorModule {{
public:
    {name}SensorModule();
    void setup() override;
    void readData() override;
}};

#endif // {name.upper()}_SENSOR_MODULE_H
'''

    cpp_content = f'''#include "{name}SensorModule.h"

{name}SensorModule::{name}SensorModule() : SensorModule("{name}") {{}}

void {name}SensorModule::setup() {{
    // TODO: Initialize hardware here (e.g. Wire.begin(), etc.)
}}

void {name}SensorModule::readData() {{
    // TODO: Read data from sensor
    // addData("temperature", 25.4);
    // addData("humidity", 60.1);
}}
'''

    encoder_content = f'''#include "PayloadEncoder.h"
#include "{name}SensorModule.h"

class {name}Encoder : public PayloadEncoder {{
public:
    void encode(std::vector<uint8_t>& payload, SensorModule* module) override {{
        // TODO: Map module data to LoRaWAN bytes
        // float temp = module->getData("temperature");
        // payload.push_back((uint8_t)temp);
    }}
}};
'''

    instructions = f'''To fully integrate your new {name} sensor:

1. Copy the generated files:
   - {name}SensorModule.h -> src/sensors/
   - {name}SensorModule.cpp -> src/sensors/
   - {name}Encoder.cpp -> src/payloads/

2. Open src/framework/SensorRegistry.cpp and add:
   #include "../sensors/{name}SensorModule.h"
   // Inside registerSensors():
   registry["{name}"] = []() -> SensorModule* {{ return new {name}SensorModule(); }};

3. Open src/framework/PayloadRegistry.cpp and add:
   #include "../payloads/{name}Encoder.cpp"
   // Inside registerEncoders():
   registry["{name}"] = []() -> PayloadEncoder* {{ return new {name}Encoder(); }};

4. Open BuildConfig.h and enable it:
   #define ENABLE_{name.upper()} 1
'''

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "a", zipfile.ZIP_DEFLATED, False) as zip_file:
        zip_file.writestr(f"src/sensors/{name}SensorModule.h", h_content)
        zip_file.writestr(f"src/sensors/{name}SensorModule.cpp", cpp_content)
        zip_file.writestr(f"src/payloads/{name}Encoder.cpp", encoder_content)
        zip_file.writestr("instructions.txt", instructions)

    zip_buffer.seek(0)
    
    return StreamingResponse(
        zip_buffer, 
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename={name}_Firmware_Template.zip"
        }
    )