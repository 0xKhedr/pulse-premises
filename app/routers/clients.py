import sqlite3

from fastapi import APIRouter, HTTPException, Request

from app.db.connection import get_db_connection as db
from app.main import (
    create_password_hash,
    delete_by_ids,
    get_current_user_from_request,
    get_floorplan_ids_for_floors_and_rooms,
    get_ids,
    get_room_ids_for_floors,
    log_audit_event,
    safe_unassign_gateways,
    safe_user_dict,
)

router = APIRouter()

@router.put("/users/{user_id}/credentials")
def update_user_credentials(user_id: int, data: dict, request: Request):
    current_user = get_current_user_from_request(request)

    if not current_user:
        raise HTTPException(status_code=401, detail="Not logged in")

    if (current_user.get("role") or "").lower() != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")

    name = data.get("name")
    email = data.get("email")
    password = data.get("password")
    enabled = data.get("enabled")

    conn = db()
    try:

        user_row = conn.execute("""
        SELECT *
        FROM users
        WHERE id = ?
        LIMIT 1
    """, (user_id,)).fetchone()

        if not user_row:
            conn.close()
            raise HTTPException(status_code=404, detail="User not found")

        old_user = dict(user_row)

        if (old_user.get("role") or "").lower() != "client":
            conn.close()
            raise HTTPException(
                status_code=400,
                detail="This endpoint is only for client user credentials"
            )

        updates = []
        params = []
        changed_fields = {}

        if name is not None:
            clean_name = str(name).strip()

            if not clean_name:
                conn.close()
                raise HTTPException(status_code=400, detail="Name cannot be empty")

            updates.append("name = ?")
            params.append(clean_name)
            changed_fields["name"] = {
                "old": old_user.get("name"),
                "new": clean_name
            }

        if email is not None:
            clean_email = str(email).strip().lower()

            if not clean_email:
                conn.close()
                raise HTTPException(status_code=400, detail="Email cannot be empty")

            existing_email = conn.execute("""
            SELECT id
            FROM users
            WHERE email = ?
              AND id != ?
            LIMIT 1
        """, (clean_email, user_id)).fetchone()

            if existing_email:
                conn.close()
                raise HTTPException(
                    status_code=409,
                    detail="Another user already uses this email"
                )

            updates.append("email = ?")
            params.append(clean_email)
            changed_fields["email"] = {
                "old": old_user.get("email"),
                "new": clean_email
            }

        if enabled is not None:
            enabled_value = 1 if enabled in [1, "1", True, "true", "True", "yes", "on"] else 0

            updates.append("enabled = ?")
            params.append(enabled_value)
            changed_fields["enabled"] = {
                "old": old_user.get("enabled"),
                "new": enabled_value
            }

        if password is not None and str(password).strip() != "":
            clean_password = str(password)

            if len(clean_password) < 8:
                conn.close()
                raise HTTPException(
                    status_code=400,
                    detail="Password must be at least 8 characters"
                )

            password_data = create_password_hash(clean_password)

            updates.append("password_salt = ?")
            params.append(password_data["password_salt"])

            updates.append("password_hash = ?")
            params.append(password_data["password_hash"])

            updates.append("password_iterations = ?")
            params.append(password_data["password_iterations"])

            updates.append("password_updated_at = CURRENT_TIMESTAMP")

            changed_fields["password"] = {
                "old": "***hidden***",
                "new": "***updated***"
            }

        if not updates:
            conn.close()
            raise HTTPException(status_code=400, detail="No credential fields provided")

        params.append(user_id)

        conn.execute(f"""
        UPDATE users
        SET {", ".join(updates)}
        WHERE id = ?
    """, params)

        if ("password" in changed_fields) or (changed_fields.get("enabled", {}).get("new") == 0):
            conn.execute("""
            UPDATE auth_sessions
            SET revoked_at = CURRENT_TIMESTAMP
            WHERE user_id = ? AND revoked_at IS NULL
        """, (user_id,))

        conn.commit()

        updated_user_row = conn.execute("""
        SELECT *
        FROM users
        WHERE id = ?
        LIMIT 1
    """, (user_id,)).fetchone()

        updated_user = safe_user_dict(updated_user_row)

        log_audit_event(
            conn=conn,
            action="update_client_credentials",
            actor=current_user.get("email", "admin"),
            target_type="user",
            target_id=user_id,
            user_id=user_id,
            details={
                "old": {
                    "id": old_user.get("id"),
                    "name": old_user.get("name"),
                    "email": old_user.get("email"),
                    "role": old_user.get("role"),
                    "enabled": old_user.get("enabled"),
                    "password_updated_at": old_user.get("password_updated_at")
                },
                "new": updated_user,
                "changed_fields": changed_fields
            }
        )

        conn.close()

        return {
            "status": "credentials_updated",
            "user": updated_user,
            "changed_fields": changed_fields
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
@router.post("/clients")
def create_client(data: dict):
    name = data.get("name", "").strip()

    if not name:
        raise HTTPException(status_code=400, detail="Client name required")

    conn = db()
    try:

        cur = conn.execute("""
        INSERT INTO clients(name)
        VALUES(?)
    """, (name,))

        conn.commit()

        client_id = cur.lastrowid

        conn.close()

        return {
            "status": "created",
            "client_id": client_id,
            "name": name,
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
@router.get("/clients")
def get_clients():
    conn = db()
    try:

        rows = conn.execute("""
        SELECT *
        FROM clients
        ORDER BY id DESC
    """).fetchall()

        conn.close()

        return [dict(r) for r in rows]
    finally:
        conn.close()
@router.post("/users")
def create_user(data: dict):
    name = data.get("name", "").strip()
    email = data.get("email", "").strip().lower()
    role = data.get("role", "client").strip()
    enabled = data.get("enabled", 1)

    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    if not email:
        raise HTTPException(status_code=400, detail="email is required")

    conn = db()
    try:

        try:
            cur = conn.execute("""
            INSERT INTO users(
                name,
                email,
                role,
                enabled
            )
            VALUES (?, ?, ?, ?)
        """, (
                name,
                email,
                role,
                enabled
            ))

            conn.commit()

            user_id = cur.lastrowid

            row = conn.execute("""
            SELECT *
            FROM users
            WHERE id = ?
        """, (user_id,)).fetchone()

            log_audit_event(
                conn,
                action="create_user",
                target_type="user",
                target_id=user_id,
                details={
                    "name": name,
                    "email": email,
                    "role": role,
                    "enabled": enabled
                }
            )

            conn.close()

            return {
                "status": "created",
                "user": dict(row)
            }

        except sqlite3.IntegrityError:
            conn.close()
            raise HTTPException(status_code=400, detail="User email already exists")
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
@router.get("/users")
def get_users():
    conn = db()
    try:

        rows = conn.execute("""
        SELECT *
        FROM users
        ORDER BY id DESC
    """).fetchall()

        conn.close()

        result = []

        for row in rows:
            result.append(safe_user_dict(row))

        return result
    finally:
        conn.close()
@router.put("/users/{user_id}")
def update_user(user_id: int, data: dict):
    conn = db()
    try:

        row = conn.execute("""
        SELECT *
        FROM users
        WHERE id = ?
    """, (user_id,)).fetchone()

        if not row:
            conn.close()
            raise HTTPException(status_code=404, detail="User not found")

        old_user = dict(row)

        name = data.get("name", row["name"])
        email = data.get("email", row["email"]).strip().lower()
        role = data.get("role", row["role"])
        enabled = data.get("enabled", row["enabled"])

        try:
            conn.execute("""
            UPDATE users
            SET name = ?,
                email = ?,
                role = ?,
                enabled = ?
            WHERE id = ?
        """, (
                name,
                email,
                role,
                enabled,
                user_id
            ))

            conn.commit()

            updated = conn.execute("""
            SELECT *
            FROM users
            WHERE id = ?
        """, (user_id,)).fetchone()

            log_audit_event(
                conn,
                action="update_user",
                target_type="user",
                target_id=user_id,
                details={
                    "old": old_user,
                    "new": dict(updated)
                }
            )

            conn.close()

            return {
                "status": "updated",
                "user": dict(updated)
            }

        except sqlite3.IntegrityError:
            conn.close()
            raise HTTPException(status_code=400, detail="User email already exists")
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
@router.post("/users/{user_id}/disable")
def disable_user(user_id: int):
    conn = db()
    try:

        row = conn.execute("""
        SELECT *
        FROM users
        WHERE id = ?
    """, (user_id,)).fetchone()

        if not row:
            conn.close()
            raise HTTPException(status_code=404, detail="User not found")

        conn.execute("""
        UPDATE users
        SET enabled = 0
        WHERE id = ?
    """, (user_id,))

        conn.execute("""
        UPDATE auth_sessions
        SET revoked_at = CURRENT_TIMESTAMP
        WHERE user_id = ? AND revoked_at IS NULL
    """, (user_id,))

        conn.commit()



        log_audit_event(
            conn,
            action="disable_user",
            target_type="user",
            target_id=user_id,
            details={
                "name": row["name"],
                "email": row["email"]
            }
        )

        conn.close()

        return {
            "status": "disabled",
            "user_id": user_id
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
@router.post("/users/{user_id}/enable")
def enable_user(user_id: int):
    conn = db()
    try:

        row = conn.execute("""
        SELECT *
        FROM users
        WHERE id = ?
    """, (user_id,)).fetchone()

        if not row:
            conn.close()
            raise HTTPException(status_code=404, detail="User not found")

        conn.execute("""
        UPDATE users
        SET enabled = 1
        WHERE id = ?
    """, (user_id,))

        conn.commit()

        log_audit_event(
            conn,
            action="enable_user",
            target_type="user",
            target_id=user_id,
            details={
                "name": row["name"],
                "email": row["email"]
            }
        )

        conn.close()

        return {
            "status": "enabled",
            "user_id": user_id
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
@router.delete("/users/{user_id}")
def delete_user(user_id: int):
    conn = db()
    try:

        row = conn.execute("""
        SELECT *
        FROM users
        WHERE id = ?
    """, (user_id,)).fetchone()

        if not row:
            conn.close()
            raise HTTPException(status_code=404, detail="User not found")

        deleted_user = dict(row)

        access_rows = conn.execute("""
        SELECT *
        FROM user_access
        WHERE user_id = ?
    """, (user_id,)).fetchall()

        deleted_access_records = [dict(a) for a in access_rows]

        conn.execute("""
        DELETE FROM user_access
        WHERE user_id = ?
    """, (user_id,))

        # Revoke all active sessions so the deleted user cannot continue using existing tokens
        conn.execute("""
        DELETE FROM auth_sessions
        WHERE user_id = ?
    """, (user_id,))

        conn.execute("""
        DELETE FROM users
        WHERE id = ?
    """, (user_id,))

        conn.commit()

        log_audit_event(
            conn,
            action="delete_user",
            target_type="user",
            target_id=user_id,
            details={
                "deleted_user": deleted_user,
                "deleted_access_records": deleted_access_records
            }
        )

        conn.close()

        return {
            "status": "deleted",
            "user_id": user_id,
            "deleted_access_records": True
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
def normalize_user_access_scope(
    conn,
    client_id,
    site_id,
    building_id,
    floor_id,
):
    """Resolve one access scope to a consistent hierarchy ancestry."""
    if floor_id:
        row = conn.execute(
            """
            SELECT f.id AS floor_id, b.id AS building_id,
                   s.id AS site_id, c.id AS client_id
            FROM floors f
            JOIN buildings b ON b.id = f.building_id
            JOIN sites s ON s.id = b.site_id
            JOIN clients c ON c.id = s.client_id
            WHERE f.id = ?
            """,
            (floor_id,),
        ).fetchone()
    elif building_id:
        row = conn.execute(
            """
            SELECT NULL AS floor_id, b.id AS building_id,
                   s.id AS site_id, c.id AS client_id
            FROM buildings b
            JOIN sites s ON s.id = b.site_id
            JOIN clients c ON c.id = s.client_id
            WHERE b.id = ?
            """,
            (building_id,),
        ).fetchone()
    elif site_id:
        row = conn.execute(
            """
            SELECT NULL AS floor_id, NULL AS building_id,
                   s.id AS site_id, c.id AS client_id
            FROM sites s
            JOIN clients c ON c.id = s.client_id
            WHERE s.id = ?
            """,
            (site_id,),
        ).fetchone()
    elif client_id:
        row = conn.execute(
            """
            SELECT NULL AS floor_id, NULL AS building_id,
                   NULL AS site_id, id AS client_id
            FROM clients
            WHERE id = ?
            """,
            (client_id,),
        ).fetchone()
    else:
        raise HTTPException(status_code=400, detail="At least one access scope is required")

    if not row:
        raise HTTPException(status_code=404, detail="Access scope not found")

    resolved = dict(row)
    supplied = {
        "client_id": client_id,
        "site_id": site_id,
        "building_id": building_id,
        "floor_id": floor_id,
    }
    for key, value in supplied.items():
        if value is not None and value != resolved[key]:
            raise HTTPException(
                status_code=400,
                detail=f"{key} does not belong to the selected access scope",
            )
    return (
        resolved["client_id"],
        resolved["site_id"],
        resolved["building_id"],
        resolved["floor_id"],
    )


@router.post("/user-access")
def create_user_access(data: dict):
    user_id = data.get("user_id")
    client_id = data.get("client_id")
    site_id = data.get("site_id")
    building_id = data.get("building_id")
    floor_id = data.get("floor_id")

    access_level = data.get("access_level", "viewer")

    can_view_devices = data.get("can_view_devices", 1)
    can_view_gateways = data.get("can_view_gateways", 1)
    can_view_alarms = data.get("can_view_alarms", 1)
    can_view_telemetry = data.get("can_view_telemetry", 1)
    can_manage_email_settings = data.get("can_manage_email_settings", 0)

    if not user_id:
        raise HTTPException(status_code=400, detail="user_id is required")

    if not client_id and not site_id and not building_id and not floor_id:
        raise HTTPException(
            status_code=400,
            detail="At least one access scope is required: client_id, site_id, building_id, or floor_id"
        )

    conn = db()
    try:

        user = conn.execute("""
        SELECT *
        FROM users
        WHERE id = ?
    """, (user_id,)).fetchone()

        if not user:
            conn.close()
            raise HTTPException(status_code=404, detail="User not found")

        client_id, site_id, building_id, floor_id = normalize_user_access_scope(
            conn,
            client_id,
            site_id,
            building_id,
            floor_id,
        )

        existing = conn.execute("""
        SELECT *
        FROM user_access
        WHERE user_id = ?
          AND IFNULL(client_id, 0) = IFNULL(?, 0)
          AND IFNULL(site_id, 0) = IFNULL(?, 0)
          AND IFNULL(building_id, 0) = IFNULL(?, 0)
          AND IFNULL(floor_id, 0) = IFNULL(?, 0)
    """, (
            user_id,
            client_id,
            site_id,
            building_id,
            floor_id
        )).fetchone()

        if existing:
            conn.close()
            return {
                "status": "already_exists",
                "access": dict(existing)
            }

        cur = conn.execute("""
        INSERT INTO user_access(
            user_id,
            client_id,
            site_id,
            building_id,
            floor_id,
            access_level,
            can_view_devices,
            can_view_gateways,
            can_view_alarms,
            can_view_telemetry,
            can_manage_email_settings
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
            user_id,
            client_id,
            site_id,
            building_id,
            floor_id,
            access_level,
            can_view_devices,
            can_view_gateways,
            can_view_alarms,
            can_view_telemetry,
            can_manage_email_settings
        ))

        conn.commit()

        access_id = cur.lastrowid

        row = conn.execute("""
        SELECT *
        FROM user_access
        WHERE id = ?
    """, (access_id,)).fetchone()

        log_audit_event(
            conn,
            action="create_user_access",
            target_type="user_access",
            target_id=access_id,
            details={
                "user": {
                    "id": user["id"],
                    "name": user["name"],
                    "email": user["email"]
                },
                "access": dict(row)
            }
        )

        conn.close()

        return {
            "status": "created",
            "access": dict(row)
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
@router.get("/user-access")
def get_all_user_access():
    conn = db()
    try:

        rows = conn.execute("""
        SELECT
            ua.*,
            u.name AS user_name,
            u.email AS user_email
        FROM user_access ua
        JOIN users u ON u.id = ua.user_id
        ORDER BY ua.id DESC
    """).fetchall()

        conn.close()

        return [dict(r) for r in rows]
    finally:
        conn.close()
@router.get("/users/{user_id}/access")
def get_user_access(user_id: int):
    conn = db()
    try:

        user = conn.execute("""
        SELECT *
        FROM users
        WHERE id = ?
    """, (user_id,)).fetchone()

        if not user:
            conn.close()
            raise HTTPException(status_code=404, detail="User not found")

        rows = conn.execute("""
        SELECT *
        FROM user_access
        WHERE user_id = ?
        ORDER BY id DESC
    """, (user_id,)).fetchall()

        conn.close()

        return {
            "user": dict(user),
            "access": [dict(r) for r in rows]
        }
    finally:
        conn.close()
@router.put("/user-access/{access_id}")
def update_user_access(access_id: int, data: dict):
    conn = db()
    try:

        row = conn.execute("""
        SELECT *
        FROM user_access
        WHERE id = ?
    """, (access_id,)).fetchone()

        if not row:
            conn.close()
            raise HTTPException(status_code=404, detail="Access record not found")

        old_access = dict(row)

        client_id = data.get("client_id", row["client_id"])
        site_id = data.get("site_id", row["site_id"])
        building_id = data.get("building_id", row["building_id"])
        floor_id = data.get("floor_id", row["floor_id"])

        client_id, site_id, building_id, floor_id = normalize_user_access_scope(
            conn,
            client_id,
            site_id,
            building_id,
            floor_id,
        )

        access_level = data.get("access_level", row["access_level"])

        can_view_devices = data.get("can_view_devices", row["can_view_devices"])
        can_view_gateways = data.get("can_view_gateways", row["can_view_gateways"])
        can_view_alarms = data.get("can_view_alarms", row["can_view_alarms"])
        can_view_telemetry = data.get("can_view_telemetry", row["can_view_telemetry"])
        can_manage_email_settings = data.get(
            "can_manage_email_settings",
            row["can_manage_email_settings"]
        )

        conn.execute("""
        UPDATE user_access
        SET client_id = ?,
            site_id = ?,
            building_id = ?,
            floor_id = ?,
            access_level = ?,
            can_view_devices = ?,
            can_view_gateways = ?,
            can_view_alarms = ?,
            can_view_telemetry = ?,
            can_manage_email_settings = ?
        WHERE id = ?
    """, (
            client_id,
            site_id,
            building_id,
            floor_id,
            access_level,
            can_view_devices,
            can_view_gateways,
            can_view_alarms,
            can_view_telemetry,
            can_manage_email_settings,
            access_id
        ))

        conn.commit()

        updated = conn.execute("""
        SELECT *
        FROM user_access
        WHERE id = ?
    """, (access_id,)).fetchone()

        new_access = dict(updated)

        log_audit_event(
            conn,
            action="update_user_access",
            target_type="user_access",
            target_id=access_id,
            details={
                "old": old_access,
                "new": new_access
            },
            client_id=new_access.get("client_id"),
            site_id=new_access.get("site_id"),
            building_id=new_access.get("building_id"),
            floor_id=new_access.get("floor_id"),
            user_id=new_access.get("user_id")
        )

        conn.close()

        return {
            "status": "updated",
            "access": new_access
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
@router.delete("/user-access/{access_id}")
def delete_user_access(access_id: int):
    conn = db()
    try:

        row = conn.execute("""
        SELECT *
        FROM user_access
        WHERE id = ?
    """, (access_id,)).fetchone()

        if not row:
            conn.close()
            raise HTTPException(status_code=404, detail="Access record not found")

        deleted_access = dict(row)

        conn.execute("""
        DELETE FROM user_access
        WHERE id = ?
    """, (access_id,))

        conn.commit()

        log_audit_event(
            conn,
            action="delete_user_access",
            target_type="user_access",
            target_id=access_id,
            details={
                "deleted_access": deleted_access
            }
        )

        conn.close()

        return {
            "status": "deleted",
            "access_id": access_id
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
@router.delete("/clients/{client_id}")
def delete_client(client_id: int):
    conn = db()
    try:

        client = conn.execute("""
        SELECT *
        FROM clients
        WHERE id = ?
    """, (client_id,)).fetchone()

        if not client:
            conn.close()
            raise HTTPException(status_code=404, detail="Client not found")

        site_ids = get_ids(conn, """
        SELECT id
        FROM sites
        WHERE client_id = ?
    """, (client_id,))

        building_ids = []

        if site_ids:
            placeholders = ",".join("?" for _ in site_ids)
            building_ids = get_ids(conn, f"""
            SELECT id
            FROM buildings
            WHERE site_id IN ({placeholders})
        """, site_ids)

        floor_ids = []

        if building_ids:
            placeholders = ",".join("?" for _ in building_ids)
            floor_ids = get_ids(conn, f"""
            SELECT id
            FROM floors
            WHERE building_id IN ({placeholders})
        """, building_ids)

        room_ids = get_room_ids_for_floors(conn, floor_ids)
        floorplan_ids = get_floorplan_ids_for_floors_and_rooms(conn, floor_ids, room_ids)

        devices_count = conn.execute("""
        SELECT COUNT(*)
        FROM devices
        WHERE client_id = ?
    """, (client_id,)).fetchone()[0]

        conn.execute("""
        UPDATE devices
        SET is_placed = 0,
            client_id = NULL,
            site_id = NULL,
            building_id = NULL,
            floor_id = NULL,
            room_id = NULL,
            building = NULL,
            floor = NULL,
            room = NULL,
            x = NULL,
            y = NULL
        WHERE client_id = ?
    """, (client_id,))

        gateways_unassigned = safe_unassign_gateways(
            conn,
            client_ids=[client_id],
            site_ids=site_ids,
            building_ids=building_ids,
            floor_ids=floor_ids,
            clear_client=True,
            clear_site=True,
            clear_building=True,
            clear_floor=True
        )

        deleted_user_access = conn.execute("""
        DELETE FROM user_access
        WHERE client_id = ?
    """, (client_id,)).rowcount

        if site_ids:
            placeholders = ",".join("?" for _ in site_ids)
            deleted_user_access += conn.execute(f"""
            DELETE FROM user_access
            WHERE site_id IN ({placeholders})
        """, site_ids).rowcount

            deleted_site_maps = conn.execute(f"""
            DELETE FROM site_maps
            WHERE site_id IN ({placeholders})
        """, site_ids).rowcount
        else:
            deleted_site_maps = 0

        if building_ids:
            placeholders = ",".join("?" for _ in building_ids)
            deleted_user_access += conn.execute(f"""
            DELETE FROM user_access
            WHERE building_id IN ({placeholders})
        """, building_ids).rowcount

        if floor_ids:
            placeholders = ",".join("?" for _ in floor_ids)
            deleted_user_access += conn.execute(f"""
            DELETE FROM user_access
            WHERE floor_id IN ({placeholders})
        """, floor_ids).rowcount

        deleted_rooms = delete_by_ids(conn, "rooms", room_ids)
        deleted_floorplans = delete_by_ids(conn, "floorplans", floorplan_ids)
        deleted_floors = delete_by_ids(conn, "floors", floor_ids)
        deleted_buildings = delete_by_ids(conn, "buildings", building_ids)
        deleted_sites = delete_by_ids(conn, "sites", site_ids)

        conn.execute("""
        DELETE FROM clients
        WHERE id = ?
    """, (client_id,))

        conn.commit()
        conn.close()

        return {
            "status": "deleted",
            "client_id": client_id,
            "deleted": {
                "sites": deleted_sites,
                "buildings": deleted_buildings,
                "floors": deleted_floors,
                "rooms": deleted_rooms,
                "floorplans": deleted_floorplans,
                "site_maps": deleted_site_maps,
                "user_access_records": deleted_user_access
            },
            "devices_preserved_and_unassigned": devices_count,
            "gateways_preserved_and_unassigned": gateways_unassigned
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
@router.put("/clients/{client_id}")
def update_client(client_id: int, data: dict):
    name = data.get("name", "").strip()

    if not name:
        raise HTTPException(status_code=400, detail="Client name required")

    conn = db()
    try:

        cur = conn.execute("""
        UPDATE clients
        SET name = ?
        WHERE id = ?
    """, (name, client_id))

        conn.commit()
        updated = cur.rowcount
        conn.close()

        if updated == 0:
            raise HTTPException(status_code=404, detail="Client not found")

        return {
            "status": "updated",
            "client_id": client_id,
            "name": name,
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
