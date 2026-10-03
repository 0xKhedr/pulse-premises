import io
import json
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.db.connection import get_db_connection as db
from app.main import (
    ADMIN_PASSWORD,
    get_device_scope_for_audit,
    get_sensor_profile_detail,
    log_audit_event,
    sensor_profile_actor_from_request,
)
from app.services.alarm_overrides import (
    describe_rule,
    load_overrides,
    normalize_override_payload,
)
from app.services.export_safety import SafeDictWriter

router = APIRouter()

@router.get("/alarms/export.csv")
def export_alarm_history_csv(
    limit: int = 1000,
    device_id: str = None,
    node_type: str = None,
    alarm_type: str = None,
    building: str = None,
    floor: str = None,
    room: str = None,
    acknowledged: int = None,
    resolved: int = None,
    search: str = None,
    from_date: str = None,
    to_date: str = None
):
    conn = db()
    try:

        where_clauses = []
        params = []

        def add_filter(column_name, value):
            if value is not None and value != "":
                where_clauses.append(f"{column_name} = ?")
                params.append(value)

        add_filter("device_id", device_id)
        add_filter("node_type", node_type)
        add_filter("alarm_type", alarm_type)
        add_filter("building", building)
        add_filter("floor", floor)
        add_filter("room", room)
        add_filter("acknowledged", acknowledged)
        add_filter("resolved", resolved)

        if search:
            where_clauses.append("""
            (
                device_id LIKE ?
                OR node_type LIKE ?
                OR building LIKE ?
                OR floor LIKE ?
                OR room LIKE ?
                OR alarm_type LIKE ?
                OR alarm_message LIKE ?
                OR telemetry LIKE ?
            )
        """)

            search_value = f"%{search}%"

            params.extend([
                search_value,
                search_value,
                search_value,
                search_value,
                search_value,
                search_value,
                search_value,
                search_value
            ])

        if from_date:
            where_clauses.append("triggered_at >= ?")
            params.append(from_date)

        if to_date:
            where_clauses.append("triggered_at <= ?")

            if len(to_date) == 10:
                params.append(to_date + " 23:59:59")
            else:
                params.append(to_date)

        where_sql = ""

        if where_clauses:
            where_sql = "WHERE " + " AND ".join(where_clauses)

        if limit < 1:
            limit = 1000

        limit = min(limit, 5000)

        rows = conn.execute(f"""
        SELECT *
        FROM alarm_history
        {where_sql}
        ORDER BY datetime(triggered_at) DESC, id DESC
        LIMIT ?
    """, params + [limit]).fetchall()

        output = io.StringIO()

        fieldnames = [
            "id",
            "device_id",
            "node_type",
            "building",
            "floor",
            "room",
            "alarm_type",
            "alarm_message",
            "triggered_at",
            "acknowledged",
            "acknowledged_by",
            "acknowledged_at",
            "resolved",
            "resolved_by",
            "resolved_at",
            "telemetry"
        ]

        writer = SafeDictWriter(output, fieldnames=fieldnames)
        writer.writeheader()

        for row in rows:
            item = dict(row)

            try:
                telemetry_obj = json.loads(item["telemetry"]) if item.get("telemetry") else {}
                telemetry_text = json.dumps(telemetry_obj, ensure_ascii=False)
            except Exception:
                telemetry_text = item.get("telemetry") or ""

            writer.writerow({
                "id": item.get("id"),
                "device_id": item.get("device_id"),
                "node_type": item.get("node_type"),
                "building": item.get("building"),
                "floor": item.get("floor"),
                "room": item.get("room"),
                "alarm_type": item.get("alarm_type"),
                "alarm_message": item.get("alarm_message"),
                "triggered_at": item.get("triggered_at"),
                "acknowledged": item.get("acknowledged"),
                "acknowledged_by": item.get("acknowledged_by"),
                "acknowledged_at": item.get("acknowledged_at"),
                "resolved": item.get("resolved"),
                "resolved_by": item.get("resolved_by"),
                "resolved_at": item.get("resolved_at"),
                "telemetry": telemetry_text
            })

        conn.close()

        output.seek(0)

        export_date = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"alarm_history_export_{export_date}.csv"

        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename={filename}"
            }
        )    
    finally:
        conn.close()
@router.get("/alarms")
def get_alarms(status: str | None = None):
    conn = db()
    try:

        query = """
        SELECT *
        FROM alarm_history
    """
        params = []

        if status == "active":
            query += " WHERE acknowledged = 0 AND resolved = 0"
        elif status == "acknowledged":
            query += " WHERE acknowledged = 1 AND resolved = 0"
        elif status == "resolved":
            query += " WHERE resolved = 1"

        query += " ORDER BY triggered_at DESC"

        rows = conn.execute(query, params).fetchall()
        conn.close()

        result = []

        for row in rows:
            item = dict(row)

            try:
                item["telemetry"] = json.loads(item["telemetry"]) if item["telemetry"] else {}
            except Exception:
                item["telemetry"] = {}

            result.append(item)

        return result
    finally:
        conn.close()
@router.post("/alarms/{alarm_id}/acknowledge")
def acknowledge_alarm(alarm_id: int, data: dict | None = None):
    acknowledged_by = (data or {}).get("acknowledged_by", "admin")

    conn = db()
    try:

        row = conn.execute("""
        SELECT *
        FROM alarm_history
        WHERE id = ?
    """, (alarm_id,)).fetchone()

        if not row:
            conn.close()
            raise HTTPException(status_code=404, detail="Alarm not found")

        old_alarm = dict(row)

        conn.execute("""
        UPDATE alarm_history
        SET acknowledged = 1,
            acknowledged_by = ?,
            acknowledged_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (acknowledged_by, alarm_id))

        conn.commit()

        updated_alarm = conn.execute("""
        SELECT *
        FROM alarm_history
        WHERE id = ?
    """, (alarm_id,)).fetchone()

        new_alarm = dict(updated_alarm)

        device_scope = get_device_scope_for_audit(
            conn,
            new_alarm.get("device_id")
        )

        log_audit_event(
            conn,
            action="acknowledge_alarm",
            actor=acknowledged_by,
            target_type="alarm",
            target_id=alarm_id,
            details={
                "old": old_alarm,
                "new": new_alarm
            },
            client_id=device_scope.get("client_id"),
            site_id=device_scope.get("site_id"),
            building_id=device_scope.get("building_id"),
            floor_id=device_scope.get("floor_id"),
            room_id=device_scope.get("room_id"),
            device_id=device_scope.get("device_id")
        )

        conn.close()

        return {
            "status": "acknowledged",
            "alarm_id": alarm_id,
            "acknowledged_by": acknowledged_by,
            "device_scope": device_scope
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
@router.post("/alarms/{alarm_id}/resolve")
def resolve_alarm(alarm_id: int, data: dict | None = None):
    resolved_by = (data or {}).get("resolved_by", "admin")

    conn = db()
    try:

        row = conn.execute("""
        SELECT *
        FROM alarm_history
        WHERE id = ?
    """, (alarm_id,)).fetchone()

        if not row:
            conn.close()
            raise HTTPException(status_code=404, detail="Alarm not found")

        old_alarm = dict(row)

        conn.execute("""
        UPDATE alarm_history
        SET resolved = 1,
            resolved_by = ?,
            resolved_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (resolved_by, alarm_id))

        conn.commit()

        updated_alarm = conn.execute("""
        SELECT *
        FROM alarm_history
        WHERE id = ?
    """, (alarm_id,)).fetchone()

        new_alarm = dict(updated_alarm)

        device_scope = get_device_scope_for_audit(
            conn,
            new_alarm.get("device_id")
        )

        log_audit_event(
            conn,
            action="resolve_alarm",
            actor=resolved_by,
            target_type="alarm",
            target_id=alarm_id,
            details={
                "old": old_alarm,
                "new": new_alarm
            },
            client_id=device_scope.get("client_id"),
            site_id=device_scope.get("site_id"),
            building_id=device_scope.get("building_id"),
            floor_id=device_scope.get("floor_id"),
            room_id=device_scope.get("room_id"),
            device_id=device_scope.get("device_id")
        )

        conn.close()

        return {
            "status": "resolved",
            "alarm_id": alarm_id,
            "resolved_by": resolved_by,
            "device_scope": device_scope
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
@router.get("/alarm-settings")
def get_alarm_settings():
    conn = db()
    try:

        recipients = conn.execute("""
        SELECT id, email, enabled, created_at
        FROM alarm_recipients
        ORDER BY id DESC
    """).fetchall()

        template = conn.execute("""
        SELECT subject_template, body_template
        FROM alarm_email_template
        WHERE id = 1
    """).fetchone()

        conn.close()

        return {
            "recipients": [
                {
                    "id": r["id"],
                    "email": r["email"],
                    "enabled": bool(r["enabled"]),
                    "created_at": r["created_at"],
                }
                for r in recipients
            ],
            "template": {
                "subject_template": template["subject_template"],
                "body_template": template["body_template"],
            } if template else None,
        }
    finally:
        conn.close()
@router.post("/alarm-settings/recipients")
def add_alarm_recipient(data: dict):
    if data.get("admin_password") != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Invalid admin password")

    email = data.get("email", "").strip()

    if not email:
        raise HTTPException(status_code=400, detail="Email is required")

    conn = db()
    try:

        conn.execute("""
        INSERT OR IGNORE INTO alarm_recipients (email, enabled)
        VALUES (?, 1)
    """, (email,))

        conn.commit()
        conn.close()

        return {
            "status": "recipient_added",
            "email": email,
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
@router.delete("/alarm-settings/recipients/{recipient_id}")
def delete_alarm_recipient(recipient_id: int, admin_password: str):
    if admin_password != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Invalid admin password")

    conn = db()
    try:

        cur = conn.execute("""
        DELETE FROM alarm_recipients
        WHERE id = ?
    """, (recipient_id,))

        conn.commit()
        deleted = cur.rowcount
        conn.close()

        if deleted == 0:
            raise HTTPException(status_code=404, detail="Recipient not found")

        return {
            "status": "recipient_deleted",
            "recipient_id": recipient_id,
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
@router.put("/alarm-settings/template")
def update_alarm_template(data: dict):
    if data.get("admin_password") != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Invalid admin password")

    subject_template = data.get("subject_template", "").strip()
    body_template = data.get("body_template", "").strip()

    if not subject_template or not body_template:
        raise HTTPException(status_code=400, detail="Subject and body templates are required")

    conn = db()
    try:

        conn.execute("""
        UPDATE alarm_email_template
        SET subject_template = ?,
            body_template = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = 1
    """, (subject_template, body_template))

        conn.commit()
        conn.close()

        return {
            "status": "template_updated",
            "subject_template": subject_template,
            "body_template": body_template,
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
@router.put("/alarm-settings/recipients/{recipient_id}/enabled")
def update_recipient_enabled(recipient_id: int, data: dict):
    if data.get("admin_password") != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Invalid admin password")

    enabled = 1 if data.get("enabled") is True else 0

    conn = db()
    try:

        cur = conn.execute("""
        UPDATE alarm_recipients
        SET enabled = ?
        WHERE id = ?
    """, (enabled, recipient_id))

        conn.commit()
        updated = cur.rowcount
        conn.close()

        if updated == 0:
            raise HTTPException(status_code=404, detail="Recipient not found")

        return {
            "status": "recipient_updated",
            "recipient_id": recipient_id,
            "enabled": bool(enabled),
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Per-device alarm thresholds
#
# Rules live on the sensor profile, so a fleet of identical sensors is managed
# as one thing. These endpoints are the escape hatch for the device that
# genuinely differs -- the cupboard that runs hot, the sensor by a fire door --
# without cloning a whole profile to hold one number. See
# app/services/alarm_overrides.py for what may and may not be patched.
# ---------------------------------------------------------------------------


def _device_row(conn, device_id: str) -> dict:
    """Fetch the device, or refuse: an override needs something to attach to."""
    row = conn.execute(
        """
        SELECT device_id, label, profile_id, profile_code
        FROM devices
        WHERE device_id = ?
        LIMIT 1
        """,
        (device_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Device not found")
    return dict(row)


def _profile_for_device(conn, device: dict, strict: bool = True):
    """
    Resolve the profile whose rules this device is judged by.

    Reading is tolerant and writing is not. Asking what can be edited on a
    device that has not been provisioned yet is a fair question with a
    plain answer -- nothing, so far -- while writing a threshold onto one
    is a mistake worth refusing, since there is no rule for it to patch.
    """
    device_id = device.get("device_id")
    profile_identifier = device.get("profile_id") or device.get(
        "profile_code")
    if not profile_identifier:
        if not strict:
            return None
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Device has no assigned sensor profile",
                "device_id": device_id,
                "action": (
                    "Assign a sensor profile before setting thresholds "
                    "for this device"
                ),
            },
        )

    profile = get_sensor_profile_detail(conn, profile_identifier)
    if not profile:
        if not strict:
            return None
        raise HTTPException(
            status_code=409,
            detail={
                "message": "The device's assigned sensor profile is missing",
                "device_id": device_id,
                "profile": str(profile_identifier),
            },
        )
    return profile


def _field_labels(profile: dict) -> dict:
    """Map field_key to the label and unit the profile declares for it."""
    labels = {}
    for field in profile.get("fields") or []:
        key = str(field.get("field_key") or "").strip().lower()
        if key:
            labels[key] = {
                "label": field.get("label") or key,
                "unit": field.get("unit"),
            }
    return labels


def _describe_device_rules(conn, device_id: str) -> dict:
    """The whole editing view for one device: defaults, patches, results."""
    device = _device_row(conn, device_id)
    profile = _profile_for_device(conn, device, strict=False)
    overrides = load_overrides(conn, device_id)

    if profile is None:
        # Nothing to edit yet, but any overrides left from a previous
        # profile are still reported so they can be cleared.
        return {
            "device_id": device["device_id"],
            "label": device.get("label"),
            "profile_id": None,
            "profile_code": None,
            "profile_name": None,
            "rules": [],
            "stale_overrides": [
                {
                    "rule_code": code,
                    "reason": "the device has no sensor profile",
                    "note": overrides[code].get("note"),
                }
                for code in sorted(overrides)
            ],
        }

    labels = _field_labels(profile)

    described = []
    matched = set()
    for rule in profile.get("rules") or []:
        code = str(rule.get("rule_code") or "").strip().upper()
        override = overrides.get(code)
        if override:
            matched.add(code)
        entry = describe_rule(rule, override)
        field_info = labels.get(
            str(rule.get("field_key") or "").strip().lower(), {}
        )
        entry["field_label"] = field_info.get("label") or rule.get("field_key")
        entry["unit"] = field_info.get("unit")
        described.append(entry)

    # An override whose rule no longer exists is left in place rather than
    # deleted behind the operator's back: it is the only remaining evidence of
    # a decision someone made, and silently discarding it during a profile
    # change would lose that.
    stale = [
        {
            "rule_code": code,
            "reason": "not in the device's current profile",
            "note": overrides[code].get("note"),
        }
        for code in sorted(set(overrides) - matched)
    ]

    return {
        "device_id": device["device_id"],
        "label": device.get("label"),
        "profile_id": profile.get("id"),
        "profile_code": profile.get("profile_code"),
        "profile_name": profile.get("profile_name"),
        "rules": described,
        "stale_overrides": stale,
    }


def _rule_by_code(profile: dict, rule_code: str) -> dict:
    wanted = str(rule_code or "").strip().upper()
    for rule in profile.get("rules") or []:
        if str(rule.get("rule_code") or "").strip().upper() == wanted:
            return rule
    raise HTTPException(
        status_code=404,
        detail={
            "message": "The device's profile has no rule with that code",
            "rule_code": wanted,
            "profile_code": profile.get("profile_code"),
        },
    )


@router.get("/alarm-overrides")
def list_devices_with_alarm_overrides():
    """
    Every device, with how many of its profile's rules it deviates from.

    Deliberately not /devices-status: that endpoint reaches out to
    ThingsBoard for each device, which is a great deal of work for what is
    only a picker.
    """
    conn = db()
    try:
        rows = conn.execute(
            """
            SELECT
                d.device_id,
                d.label,
                d.profile_code,
                p.profile_name,
                b.name AS building,
                f.name AS floor,
                r.room_name AS room,
                (
                    SELECT COUNT(*)
                    FROM sensor_profile_rules pr
                    WHERE pr.profile_id = p.id
                ) AS rule_count,
                (
                    SELECT COUNT(*)
                    FROM device_alarm_overrides o
                    WHERE o.device_id = d.device_id
                ) AS override_count
            FROM devices d
            LEFT JOIN sensor_profiles p
                   ON p.id = d.profile_id
                   OR (d.profile_id IS NULL
                       AND p.profile_code = d.profile_code)
            LEFT JOIN rooms r ON d.room_id = r.id
            LEFT JOIN floors f ON f.id = COALESCE(d.floor_id, r.floor_id)
            LEFT JOIN buildings b ON f.building_id = b.id
            ORDER BY COALESCE(d.label, d.device_id), d.device_id
            """
        ).fetchall()
        return {"devices": [dict(row) for row in rows]}
    finally:
        conn.close()


@router.get("/alarm-overrides/{device_id}")
def get_device_alarm_overrides(device_id: str):
    """One device's rules: the profile default, its patch, and the result."""
    conn = db()
    try:
        return _describe_device_rules(conn, device_id)
    finally:
        conn.close()


@router.put("/alarm-overrides/{device_id}/{rule_code}")
def set_device_alarm_override(
    device_id: str, rule_code: str, data: dict, request: Request
):
    """
    Make one rule trip differently, or not at all, for one device.

    Any field left null is inherited from the profile, so this is a patch
    rather than a copy: a device can raise one bound of a range and keep
    following the profile for everything else.
    """
    conn = db()
    try:
        profile = _profile_for_device(conn, _device_row(conn, device_id))
        rule = _rule_by_code(profile, rule_code)
        clean_code = str(rule.get("rule_code")).strip().upper()

        try:
            override = normalize_override_payload(rule, data or {})
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error))

        actor = sensor_profile_actor_from_request(request)
        previous = load_overrides(conn, device_id).get(clean_code)

        conn.execute(
            """
            INSERT INTO device_alarm_overrides (
                device_id, rule_code, threshold_value, threshold_value_2,
                enabled, note, created_by, updated_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(device_id, rule_code) DO UPDATE SET
                threshold_value = excluded.threshold_value,
                threshold_value_2 = excluded.threshold_value_2,
                enabled = excluded.enabled,
                note = excluded.note,
                updated_by = excluded.updated_by,
                updated_at = datetime('now')
            """,
            (
                device_id,
                clean_code,
                override["threshold_value"],
                override["threshold_value_2"],
                override["enabled"],
                override["note"],
                actor,
                actor,
            ),
        )

        scope = get_device_scope_for_audit(conn, device_id)
        log_audit_event(
            conn,
            action="device_alarm_override_set",
            actor=actor,
            target_type="device",
            target_id=device_id,
            details={
                "rule_code": clean_code,
                "profile_code": profile.get("profile_code"),
                "profile_default": {
                    "threshold_value": rule.get("threshold_value"),
                    "threshold_value_2": rule.get("threshold_value_2"),
                    "enabled": bool(rule.get("enabled")),
                },
                "previous": previous,
                "applied": override,
            },
            **scope,
        )
        conn.commit()

        described = _describe_device_rules(conn, device_id)
        matches = [
            entry
            for entry in described["rules"]
            if str(entry.get("rule_code") or "").strip().upper() == clean_code
        ]
        return {"status": "saved", "rule": matches[0] if matches else None}
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


@router.delete("/alarm-overrides/{device_id}/{rule_code}")
def clear_device_alarm_override(
    device_id: str, rule_code: str, request: Request
):
    """
    Return one rule to the profile default for this device.

    This accepts a rule_code the current profile no longer defines, which is
    how a leftover from a profile change is cleared.
    """
    conn = db()
    try:
        clean_code = str(rule_code or "").strip().upper()
        previous = load_overrides(conn, device_id).get(clean_code)

        cursor = conn.execute(
            """
            DELETE FROM device_alarm_overrides
            WHERE device_id = ?
              AND rule_code = ?
            """,
            (device_id, clean_code),
        )
        if cursor.rowcount == 0:
            raise HTTPException(
                status_code=404,
                detail="This device has no override for that rule",
            )

        actor = sensor_profile_actor_from_request(request)
        scope = get_device_scope_for_audit(conn, device_id)
        log_audit_event(
            conn,
            action="device_alarm_override_cleared",
            actor=actor,
            target_type="device",
            target_id=device_id,
            details={"rule_code": clean_code, "removed": previous},
            **scope,
        )
        conn.commit()

        # A stale override has no rule left to describe, so the device view is
        # only rebuilt when one can still be produced.
        rule = None
        try:
            described = _describe_device_rules(conn, device_id)
            matches = [
                entry
                for entry in described["rules"]
                if str(entry.get("rule_code") or "").strip().upper()
                == clean_code
            ]
            rule = matches[0] if matches else None
        except HTTPException:
            rule = None

        return {"status": "reverted", "rule_code": clean_code, "rule": rule}
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()

