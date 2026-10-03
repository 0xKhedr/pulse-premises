import logging

logger = logging.getLogger(__name__)


from fastapi import APIRouter

from app.db.connection import get_db_connection as db
from app.main import ALLOWED_NODE_TYPES, profile_json_load
from app.schemas.core import Device

router = APIRouter()

@router.get("/provision-options")
def provision_options():
    """
    Return the location hierarchy and all active sensor profiles
    available for LILYGO provisioning.

    Compatibility:
    - profiles: new profile-driven provisioning architecture.
    - node_types: retained for the current legacy firmware.
    """

    conn = db()

    try:
        # =====================================================
        # 1. LOAD PROVISIONABLE ROOMS
        # =====================================================

        room_rows = conn.execute(
            """
            SELECT
                rooms.id,
                buildings.name AS building,
                floors.name AS floor,
                rooms.room_name,
                rooms.x,
                rooms.y,
                rooms.floor_id
            FROM rooms
            LEFT JOIN floors ON floors.id = rooms.floor_id
            LEFT JOIN buildings ON buildings.id = floors.building_id
            WHERE rooms.floor_id IS NOT NULL
            ORDER BY
                buildings.name,
                floors.floor_number,
                rooms.room_name
            """
        ).fetchall()

        buildings = {}

        for row in room_rows:
            building_name = row["building"]
            floor_name = row["floor"]
            room_name = row["room_name"]

            if building_name not in buildings:
                buildings[building_name] = {}

            if floor_name not in buildings[building_name]:
                buildings[building_name][floor_name] = {}

            buildings[building_name][floor_name][room_name] = {
                "room_id": row["id"],
                "floor_id": row["floor_id"],
                "x": row["x"],
                "y": row["y"],
            }

        # =====================================================
        # 2. LOAD ACTIVE SENSOR PROFILES
        # =====================================================

        profile_rows = conn.execute(
            """
            SELECT
                id,
                profile_code,
                profile_name,
                profile_version,
                node_type,
                description,

                capabilities_json,
                configuration_schema_json,

                payload_encoder_key,
                payload_version,
                f_port,
                uplink_interval_seconds,

                icon_type,
                icon_color,

                status,
                enabled,
                schema_checksum

            FROM sensor_profiles

            WHERE enabled = 1
              AND status = 'active'

            ORDER BY
                profile_name,
                profile_version DESC
            """
        ).fetchall()

        profiles = []

        for profile_row in profile_rows:
            profile_id = profile_row["id"]

            # -------------------------------------------------
            # Physical sensors and firmware modules
            # -------------------------------------------------

            sensor_rows = conn.execute(
                """
                SELECT
                    relation.role,
                    relation.required,
                    relation.configuration_json,
                    relation.display_order,

                    catalog.sensor_code,
                    catalog.manufacturer,
                    catalog.model,
                    catalog.use_case,
                    catalog.protocol,
                    catalog.default_bus,
                    catalog.default_address,

                    module.module_key,
                    module.display_name AS module_name,
                    module.driver_class,
                    module.library_name,
                    module.library_version,
                    module.supported_board,
                    module.min_firmware_version

                FROM sensor_profile_sensors relation

                INNER JOIN sensor_catalog catalog
                    ON catalog.id = relation.sensor_id

                LEFT JOIN firmware_modules module
                    ON module.id = relation.firmware_module_id

                WHERE relation.profile_id = ?

                ORDER BY
                    relation.display_order,
                    relation.id
                """,
                (profile_id,),
            ).fetchall()

            sensor_components = []

            for sensor_row in sensor_rows:
                sensor_components.append(
                    {
                        "sensor_code": sensor_row[
                            "sensor_code"
                        ],
                        "manufacturer": sensor_row[
                            "manufacturer"
                        ],
                        "model": sensor_row["model"],
                        "use_case": sensor_row["use_case"],
                        "protocol": sensor_row["protocol"],
                        "default_bus": sensor_row[
                            "default_bus"
                        ],
                        "default_address": sensor_row[
                            "default_address"
                        ],
                        "role": sensor_row["role"],
                        "required": bool(
                            sensor_row["required"]
                        ),
                        "configuration": profile_json_load(
                            sensor_row[
                                "configuration_json"
                            ],
                            {},
                        ),
                        "firmware_module": {
                            "module_key": sensor_row[
                                "module_key"
                            ],
                            "module_name": sensor_row[
                                "module_name"
                            ],
                            "driver_class": sensor_row[
                                "driver_class"
                            ],
                            "library_name": sensor_row[
                                "library_name"
                            ],
                            "library_version": sensor_row[
                                "library_version"
                            ],
                            "supported_board": sensor_row[
                                "supported_board"
                            ],
                            "min_firmware_version": (
                                sensor_row[
                                    "min_firmware_version"
                                ]
                            ),
                        },
                    }
                )

            # -------------------------------------------------
            # Telemetry/payload field definitions
            # -------------------------------------------------

            field_rows = conn.execute(
                """
                SELECT
                    field_key,
                    label,
                    unit,
                    data_type,

                    payload_order,
                    byte_offset,
                    byte_length,
                    scale,
                    signed,
                    endianness,

                    required,
                    nullable,

                    min_value,
                    max_value

                FROM sensor_profile_fields

                WHERE profile_id = ?

                ORDER BY
                    payload_order,
                    display_order,
                    id
                """,
                (profile_id,),
            ).fetchall()

            telemetry_fields = []

            for field_row in field_rows:
                telemetry_fields.append(
                    {
                        "field_key": field_row["field_key"],
                        "label": field_row["label"],
                        "unit": field_row["unit"],
                        "data_type": field_row["data_type"],
                        "payload_order": field_row[
                            "payload_order"
                        ],
                        "byte_offset": field_row[
                            "byte_offset"
                        ],
                        "byte_length": field_row[
                            "byte_length"
                        ],
                        "scale": field_row["scale"],
                        "signed": bool(
                            field_row["signed"]
                        ),
                        "endianness": field_row[
                            "endianness"
                        ],
                        "required": bool(
                            field_row["required"]
                        ),
                        "nullable": bool(
                            field_row["nullable"]
                        ),
                        "min_value": field_row[
                            "min_value"
                        ],
                        "max_value": field_row[
                            "max_value"
                        ],
                    }
                )

            # -------------------------------------------------
            # Required firmware features
            # -------------------------------------------------

            compatibility_rows = conn.execute(
                """
                SELECT
                    module.module_key,
                    compatibility.min_firmware_version,
                    compatibility.max_firmware_version,
                    compatibility.hardware_revision,
                    compatibility.required_features_json

                FROM profile_firmware_compatibility compatibility

                INNER JOIN firmware_modules module
                    ON module.id = (
                        compatibility.firmware_module_id
                    )

                WHERE compatibility.profile_id = ?
                  AND compatibility.enabled = 1

                ORDER BY module.module_key
                """,
                (profile_id,),
            ).fetchall()

            firmware_compatibility = []

            for compatibility_row in compatibility_rows:
                firmware_compatibility.append(
                    {
                        "module_key": compatibility_row[
                            "module_key"
                        ],
                        "min_firmware_version": (
                            compatibility_row[
                                "min_firmware_version"
                            ]
                        ),
                        "max_firmware_version": (
                            compatibility_row[
                                "max_firmware_version"
                            ]
                        ),
                        "hardware_revision": (
                            compatibility_row[
                                "hardware_revision"
                            ]
                        ),
                        "required_features": (
                            profile_json_load(
                                compatibility_row[
                                    "required_features_json"
                                ],
                                [],
                            )
                        ),
                    }
                )

            profiles.append(
                {
                    "profile_id": profile_id,
                    "profile_code": profile_row[
                        "profile_code"
                    ],
                    "profile_name": profile_row[
                        "profile_name"
                    ],
                    "profile_version": profile_row[
                        "profile_version"
                    ],
                    "node_type": profile_row["node_type"],
                    "description": profile_row[
                        "description"
                    ],
                    "capabilities": profile_json_load(
                        profile_row[
                            "capabilities_json"
                        ],
                        [],
                    ),
                    "configuration_schema": (
                        profile_json_load(
                            profile_row[
                                "configuration_schema_json"
                            ],
                            {},
                        )
                    ),
                    "payload_encoder_key": profile_row[
                        "payload_encoder_key"
                    ],
                    "payload_version": profile_row[
                        "payload_version"
                    ],
                    "f_port": profile_row["f_port"],
                    "uplink_interval_seconds": (
                        profile_row[
                            "uplink_interval_seconds"
                        ]
                    ),
                    "icon_type": profile_row["icon_type"],
                    "icon_color": profile_row["icon_color"],
                    "schema_checksum": profile_row[
                        "schema_checksum"
                    ],
                    "sensor_components": sensor_components,
                    "telemetry_fields": telemetry_fields,
                    "firmware_compatibility": (
                        firmware_compatibility
                    ),
                }
            )

        # =====================================================
        # 3. RETAIN LEGACY NODE TYPES
        # =====================================================

        profile_node_types = {
            profile["node_type"]
            for profile in profiles
            if profile.get("node_type")
        }

        available_node_types = sorted(
            set(ALLOWED_NODE_TYPES)
            | profile_node_types
        )

        return {
            "status": "ok",
            "profile_architecture_version": 1,
            "profile_count": len(profiles),

            # New provisioning architecture
            "profiles": profiles,

            # Legacy firmware compatibility
            "node_types": available_node_types,

            # Location hierarchy
            "buildings": buildings,
        }

    finally:
        conn.close()
@router.post("/provision")
def provision(device: Device):
    """
    Provision or update a LILYGO using a backend-validated sensor
    profile and a stable room_id.
    """
    from app.services.provisioning import provision as provision_service
    return provision_service(device)
