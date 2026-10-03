from __future__ import annotations

import json
import sqlite3

PROFILE_CODE = "ESPCAM_METER_V1"

# The uplink formatter for the camera node's payload, which is one status byte
# followed by the recognized text. The network runs this and the webhook then
# receives ordinary named fields, so nothing in the application needs to know
# anything about this device's wire format.
#
# It lives here rather than only in the sketch so that a device assigned this
# profile is given the right decoder automatically. A camera registered against
# the universal Cayenne profile gets the Cayenne decoder instead, which cannot
# read this payload at all -- the uplinks arrive with no decoded fields and the
# device looks silent.
#
# The numeric test is deliberately strict. The recognizer has no confidence
# threshold and marks a glyph it could not read with '?', and parseFloat stops
# at the first bad character rather than failing: parseFloat('233?0.44') is
# 233. One unreadable digit would otherwise become a number wrong by orders of
# magnitude that looks entirely plausible on a graph. The raw text is always
# reported, so refusing to guess loses nothing.
FORMATTER_JS = """function decodeUplink(input) {
  var b = input.bytes;
  if (!b || b.length < 1) {
    return { errors: ["empty camera payload"] };
  }

  var status = b[0];

  // The camera emits digits, '.', '?' for a glyph it could not read, and '\\n'
  // between the rows of a multi-row panel. Nothing else.
  var text = "";
  for (var i = 1; i < b.length; i++) {
    text += String.fromCharCode(b[i]);
  }

  var out = {
    cam_locked:       (status & 0x01) !== 0,
    cam_segmented:    (status & 0x02) !== 0,
    cam_recognized:   (status & 0x04) !== 0,
    cam_streaming:    (status & 0x08) !== 0,
    // Bit 4 is a mask-preview frame. The camera writes the status byte as a
    // bare 0x10 in that case, so bits 0-3 are clear and it carries no reading.
    cam_mask_preview: (status & 0x10) !== 0,
    cam_reading_text: text
  };

  // A multi-row panel is two or more independent readings joined by newlines,
  // so there is no single number to report for one.
  var rows = text.split("\\n").filter(function (r) { return r.length; });
  out.cam_reading_rows = rows.length;

  var numeric = rows.length === 1 && /^[0-9]+(\\.[0-9]+)?$/.test(rows[0]);
  out.cam_reading_ok = numeric;
  if (numeric) {
    out.cam_reading = parseFloat(rows[0]);
  }

  return { data: out };
}"""

FIELDS = [
    # key, label, unit, type, order
    ("cam_reading", "Meter Reading", None, "number", 1),
    ("cam_reading_text", "Reading (raw)", None, "string", 2),
    ("cam_reading_ok", "Reading Valid", None, "boolean", 3),
    ("cam_reading_rows", "Rows Detected", None, "number", 4),
    ("cam_locked", "Camera Locked", None, "boolean", 5),
    ("cam_segmented", "Digits Segmented", None, "boolean", 6),
    ("cam_recognized", "Digits Recognized", None, "boolean", 7),
    ("cam_streaming", "Raw Preview", None, "boolean", 8),
    ("cam_mask_preview", "Mask Preview", None, "boolean", 9),
]


def up(conn: sqlite3.Connection) -> None:
    """Seed the sensor profile for ESP32-CAM meter-reading nodes.

    These nodes report a string -- the digits read off a meter panel -- which
    Cayenne LPP has no type for. The profile exists so that the decoder travels
    with the device type instead of being applied by hand: assigning it installs
    the JavaScript formatter above, and the uplinks then arrive as named fields
    the webhook already knows how to store.

    Fields are declared here with proper labels rather than left to be learned,
    so a camera reads "Meter Reading" rather than "CAM Reading" from the first
    uplink, and an alarm rule has something stable to point at.

    Idempotent: seeded only when the profile code is absent, so re-running this
    migration leaves an administrator's edits to labels or thresholds alone.
    """
    existing = conn.execute(
        "SELECT id FROM sensor_profiles WHERE profile_code = ?", (PROFILE_CODE,)
    ).fetchone()
    if existing:
        return

    # Every piece of free text is bound rather than inlined. SQL does not join
    # adjacent string literals the way Python does, so a description split
    # across source lines inside the statement is a syntax error rather than a
    # longer string.
    description = (
        "Reads a meter panel with an on-device digit classifier and reports the "
        "recognized text plus the camera pipeline status. The payload is a "
        "status byte followed by UTF-8 text, decoded by this profile's "
        "JavaScript formatter because Cayenne LPP cannot carry a string."
    )

    cursor = conn.execute(
        """
        INSERT INTO sensor_profiles (
            profile_code, profile_name, profile_version, node_type,
            description, capabilities_json, configuration_schema_json,
            payload_encoder_key, payload_version, f_port,
            uplink_interval_seconds, ttn_formatter_code, ttn_formatter_type,
            tb_device_profile_name, icon_type, icon_color, status, enabled,
            is_system, schema_checksum, created_by, updated_by
        ) VALUES (
            ?, ?, 1, 'multi',
            ?, ?, ?,
            'espcam_meter_v1', 1, 1, 30, ?, 'javascript',
            'default', 'multi', '#0ea5e9', 'active', 1, 1,
            'espcam_meter_v1_checksum', 'system', 'system'
        )
        """,
        (
            PROFILE_CODE,
            "ESP32-CAM Meter Reader",
            description,
            json.dumps(["environment", "occupancy"]),
            json.dumps({"protocol": "espcam_meter", "properties": {}}),
            FORMATTER_JS,
        ),
    )
    profile_id = cursor.lastrowid

    for field_key, label, unit, data_type, order in FIELDS:
        conn.execute(
            """
            INSERT INTO sensor_profile_fields (
                profile_id, field_key, label, unit, data_type, payload_order,
                required, nullable, display_order, visible_floor,
                visible_dashboard
            ) VALUES (?, ?, ?, ?, ?, ?, 0, 1, ?, 1, 1)
            """,
            (profile_id, field_key, label, unit, data_type, order, order),
        )
