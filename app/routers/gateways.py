import logging

logger = logging.getLogger(__name__)


from fastapi import APIRouter, HTTPException

from app.db.connection import get_db_connection as db
from app.main import (
    log_audit_event,
    ttn_get_gateway_connection_stats,
    ttn_get_gateway_status,
)

router = APIRouter()

@router.get("/gateways/{gateway_id}/sync")
def sync_gateway_status(gateway_id: str):
    # TTN is an optional upstream. If it is unreachable or rejects the
    # gateway id, record the gateway as offline rather than failing the sync
    # with a 500.
    try:
        gateway_record = ttn_get_gateway_status(gateway_id)
    except Exception as exc:
        logger.info("TTN gateway lookup failed for %s: %s", gateway_id, exc)
        gateway_record = None

    try:
        connection_stats = ttn_get_gateway_connection_stats(gateway_id)
    except Exception as exc:
        logger.info("TTN gateway stats failed for %s: %s", gateway_id, exc)
        connection_stats = None

    if connection_stats:
        status = "online"
        last_seen = connection_stats.get("connected_at")
    else:
        status = "offline"
        last_seen = None

    conn = db()
    try:

        conn.execute("""
        UPDATE gateways
        SET status = ?,
            last_seen = ?
        WHERE gateway_id = ?
    """, (
            status,
            last_seen,
            gateway_id,
        ))

        conn.commit()

        row = conn.execute("""
        SELECT *
        FROM gateways
        WHERE gateway_id = ?
    """, (gateway_id,)).fetchone()

        conn.close()

        if not row:
            raise HTTPException(
                status_code=404,
                detail="Gateway not found in backend database"
            )

        return {
            "status": "synced",
            "gateway": dict(row),
            "gateway_health": {
                "connection_status": status,
                "connected_at": last_seen,
                "stats": connection_stats
            },
            "ttn_gateway_record": gateway_record,
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
@router.get("/gateways/health")
def gateways_health(site_id: int | None = None):
    conn = db()
    try:

        if site_id is not None:
            rows = conn.execute("""
            SELECT *
            FROM gateways
            WHERE site_id = ?
            ORDER BY name
        """, (site_id,)).fetchall()
        else:
            rows = conn.execute("""
            SELECT *
            FROM gateways
            ORDER BY name
        """).fetchall()

        result = []

        for row in rows:
            gateway_id = row["gateway_id"]

            try:
                connection_stats = ttn_get_gateway_connection_stats(gateway_id)

                if connection_stats:
                    connection_status = "online"

                    last_seen = (
                        connection_stats.get("last_status_received_at")
                        or connection_stats.get("last_uplink_received_at")
                        or connection_stats.get("connected_at")
                    )

                    protocol = connection_stats.get("protocol")
                    last_status_received_at = connection_stats.get("last_status_received_at")
                    last_uplink_received_at = connection_stats.get("last_uplink_received_at")
                    last_downlink_received_at = connection_stats.get("last_downlink_received_at")
                    uplink_count = connection_stats.get("uplink_count")
                    downlink_count = connection_stats.get("downlink_count")

                    ip = None

                    remote_address = connection_stats.get("gateway_remote_address")
                    if remote_address:
                        ip = remote_address.get("ip")

                    if not ip:
                        last_status = connection_stats.get("last_status", {})
                        ip_list = last_status.get("ip", [])
                        if ip_list:
                            ip = ip_list[0]

                    error_message = None

                else:
                    connection_status = "offline"
                    last_seen = None
                    protocol = None
                    last_status_received_at = None
                    last_uplink_received_at = None
                    last_downlink_received_at = None
                    uplink_count = 0
                    downlink_count = 0
                    ip = None
                    error_message = None

            # Any upstream failure (TTN unreachable, DNS, timeout) must mark
            # this one gateway as errored, not 500 the whole dashboard.
            except Exception as e:
                connection_status = "error"
                last_seen = None
                protocol = None
                last_status_received_at = None
                last_uplink_received_at = None
                last_downlink_received_at = None
                uplink_count = 0
                downlink_count = 0
                ip = None
                error_message = str(getattr(e, "detail", e))

            conn.execute("""
            UPDATE gateways
            SET status = ?,
                last_seen = ?
            WHERE gateway_id = ?
        """, (
                connection_status,
                last_seen,
                gateway_id
            ))

            result.append({
                "id": row["id"],
                "gateway_id": gateway_id,
                "name": row["name"],
                "client_id": row["client_id"],
                "site_id": row["site_id"],
                "connection_status": connection_status,
                "last_seen": last_seen,
                "last_status_received_at": last_status_received_at,
                "last_uplink_received_at": last_uplink_received_at,
                "last_downlink_received_at": last_downlink_received_at,
                "uplink_count": uplink_count,
                "downlink_count": downlink_count,
                "protocol": protocol,
                "ip": ip,
                "error_message": error_message
            })

        conn.commit()
        conn.close()

        return result
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
@router.post("/gateways")
def create_gateway(data: dict):
    gateway_id = data.get("gateway_id", "").strip()
    name = data.get("name", "").strip()

    if not gateway_id:
        raise HTTPException(status_code=400, detail="gateway_id is required")

    if not name:
        name = gateway_id

    client_id = data.get("client_id")
    site_id = data.get("site_id")
    building_id = data.get("building_id")
    floor_id = data.get("floor_id")
    x = data.get("x")
    y = data.get("y")
    label = data.get("label")
    location_note = data.get("location_note")

    conn = db()
    try:

        existing = conn.execute("""
        SELECT *
        FROM gateways
        WHERE gateway_id = ?
    """, (gateway_id,)).fetchone()

        if existing:
            old_gateway = dict(existing)

            conn.execute("""
            UPDATE gateways
            SET
                name = ?,
                client_id = ?,
                site_id = ?,
                building_id = ?,
                floor_id = ?,
                x = ?,
                y = ?,
                label = ?,
                location_note = ?
            WHERE gateway_id = ?
        """, (
                name,
                client_id,
                site_id,
                building_id,
                floor_id,
                x,
                y,
                label,
                location_note,
                gateway_id,
            ))

            conn.commit()

            updated = conn.execute("""
            SELECT *
            FROM gateways
            WHERE gateway_id = ?
        """, (gateway_id,)).fetchone()

            log_audit_event(
                conn,
                action="update_gateway",
                target_type="gateway",
                target_id=updated["id"],
                details={
                    "old": old_gateway,
                    "new": dict(updated)
                }
            )

            conn.close()

            return {
                "status": "updated",
                "gateway": dict(updated)
            }

        cur = conn.execute("""
        INSERT INTO gateways(
            gateway_id,
            name,
            client_id,
            site_id,
            building_id,
            floor_id,
            x,
            y,
            label,
            location_note
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
            gateway_id,
            name,
            client_id,
            site_id,
            building_id,
            floor_id,
            x,
            y,
            label,
            location_note,
        ))

        conn.commit()

        gateway_db_id = cur.lastrowid

        created = conn.execute("""
        SELECT *
        FROM gateways
        WHERE id = ?
    """, (gateway_db_id,)).fetchone()

        log_audit_event(
            conn,
            action="create_gateway",
            target_type="gateway",
            target_id=gateway_db_id,
            details={
                "gateway": dict(created)
            }
        )

        conn.close()

        return {
            "status": "created",
            "gateway": dict(created)
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
@router.get("/gateways")
def get_gateways(site_id: int | None = None):
    conn = db()
    try:

        if site_id is not None:
            rows = conn.execute("""
            SELECT *
            FROM gateways
            WHERE site_id = ?
            ORDER BY name
        """, (site_id,)).fetchall()
        else:
            rows = conn.execute("""
            SELECT *
            FROM gateways
            ORDER BY name
        """).fetchall()

        conn.close()

        return [dict(r) for r in rows] 
    finally:
        conn.close()
@router.delete("/gateways/{gateway_db_id}")
def delete_gateway(gateway_db_id: int):
    conn = db()
    try:

        row = conn.execute("""
        SELECT *
        FROM gateways
        WHERE id = ?
    """, (gateway_db_id,)).fetchone()

        if not row:
            conn.close()
            raise HTTPException(
                status_code=404,
                detail="Gateway not found"
            )

        conn.execute("""
        DELETE FROM gateways
        WHERE id = ?
    """, (gateway_db_id,))

        conn.commit()
        conn.close()

        return {
            "status": "deleted",
            "deleted_gateway": dict(row)
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
@router.put("/gateways/{gateway_db_id}")
def update_gateway(gateway_db_id: int, data: dict):
    conn = db()
    try:

        row = conn.execute("""
        SELECT *
        FROM gateways
        WHERE id = ?
    """, (gateway_db_id,)).fetchone()

        if not row:
            conn.close()
            raise HTTPException(status_code=404, detail="Gateway not found")

        old_gateway = dict(row)

        name = data.get("name", row["name"])
        gateway_id = data.get("gateway_id", row["gateway_id"])
        client_id = data.get("client_id", row["client_id"])
        site_id = data.get("site_id", row["site_id"])
        building_id = data.get("building_id", row["building_id"])
        floor_id = data.get("floor_id", row["floor_id"])
        x = data.get("x", row["x"])
        y = data.get("y", row["y"])
        label = data.get("label", row["label"])
        location_note = data.get("location_note", row["location_note"])

        conn.execute("""
        UPDATE gateways
        SET gateway_id = ?,
            name = ?,
            client_id = ?,
            site_id = ?,
            building_id = ?,
            floor_id = ?,
            x = ?,
            y = ?,
            label = ?,
            location_note = ?
        WHERE id = ?
    """, (
            gateway_id,
            name,
            client_id,
            site_id,
            building_id,
            floor_id,
            x,
            y,
            label,
            location_note,
            gateway_db_id
        ))

        conn.commit()

        updated = conn.execute("""
        SELECT *
        FROM gateways
        WHERE id = ?
    """, (gateway_db_id,)).fetchone()

        new_gateway = dict(updated)

        tracked_fields = [
            "gateway_id",
            "name",
            "client_id",
            "site_id",
            "building_id",
            "floor_id",
            "x",
            "y",
            "label",
            "location_note"
        ]

        changed_fields = {}

        for field in tracked_fields:
            if old_gateway.get(field) != new_gateway.get(field):
                changed_fields[field] = {
                    "old": old_gateway.get(field),
                    "new": new_gateway.get(field)
                }

        if changed_fields:
            action_name = "update_gateway"

            if "x" in changed_fields or "y" in changed_fields:
                action_name = "move_gateway"

            log_audit_event(
                conn,
                action=action_name,
                target_type="gateway",
                target_id=gateway_db_id,
                details={
                    "changed_fields": changed_fields,
                    "old": old_gateway,
                    "new": new_gateway
                },
                client_id=new_gateway.get("client_id"),
                site_id=new_gateway.get("site_id"),
                building_id=new_gateway.get("building_id"),
                floor_id=new_gateway.get("floor_id"),
                gateway_id=new_gateway.get("gateway_id")
            )

        conn.close()

        return {
            "status": "updated" if changed_fields else "no_change",
            "gateway": new_gateway,
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