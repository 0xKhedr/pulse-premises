import io
import json
from datetime import datetime

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.db.connection import get_db_connection as db
from app.main import build_audit_message
from app.services.export_safety import SafeDictWriter, redact_secrets

router = APIRouter()

@router.get("/audit-log")
def get_audit_log(
    limit: int = 100,
    action: str = None,
    target_type: str = None,
    target_id: str = None,
    actor: str = None,
    client_id: int = None,
    site_id: int = None,
    building_id: int = None,
    floor_id: int = None,
    room_id: int = None,
    device_id: str = None,
    gateway_id: str = None,
    user_id: int = None,
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

        add_filter("action", action)
        add_filter("target_type", target_type)
        add_filter("target_id", target_id)
        add_filter("actor", actor)
        add_filter("client_id", client_id)
        add_filter("site_id", site_id)
        add_filter("building_id", building_id)
        add_filter("floor_id", floor_id)
        add_filter("room_id", room_id)
        add_filter("device_id", device_id)
        add_filter("gateway_id", gateway_id)
        add_filter("user_id", user_id)

        if search:
            where_clauses.append("""
            (
                action LIKE ?
                OR target_type LIKE ?
                OR target_id LIKE ?
                OR actor LIKE ?
                OR message LIKE ?
                OR details LIKE ?
            )
        """)
            search_value = f"%{search}%"
            params.extend([
                search_value,
                search_value,
                search_value,
                search_value,
                search_value,
                search_value
            ])

        if from_date:
            where_clauses.append("created_at >= ?")
            params.append(from_date)

        if to_date:
            where_clauses.append("created_at <= ?")

            if len(to_date) == 10:
                params.append(to_date + " 23:59:59")
            else:
                params.append(to_date)

        where_sql = ""

        if where_clauses:
            where_sql = "WHERE " + " AND ".join(where_clauses)

        if limit < 1:
            limit = 100

        limit = min(limit, 500)

        rows = conn.execute(f"""
        SELECT *
        FROM audit_log
        {where_sql}
        ORDER BY datetime(created_at) DESC, id DESC
        LIMIT ?
    """, params + [limit]).fetchall()

        conn.close()

        result = []

        for row in rows:
            item = dict(row)

            try:
                item["details"] = json.loads(item["details"]) if item["details"] else {}
            except Exception:
                item["details"] = {}
            item["details"] = redact_secrets(item["details"])

            if not item.get("message"):
                item["message"] = build_audit_message(
                    action=item.get("action"),
                    actor=item.get("actor"),
                    target_type=item.get("target_type"),
                    target_id=item.get("target_id"),
                    details=item.get("details")
                )

            item["timestamp"] = item.get("created_at")

            result.append(item)

        return result
    finally:
        conn.close()
@router.get("/audit-log/export.csv")
def export_audit_log_csv(
    limit: int = 1000,
    action: str = None,
    target_type: str = None,
    target_id: str = None,
    actor: str = None,
    client_id: int = None,
    site_id: int = None,
    building_id: int = None,
    floor_id: int = None,
    room_id: int = None,
    device_id: str = None,
    gateway_id: str = None,
    user_id: int = None,
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

        add_filter("action", action)
        add_filter("target_type", target_type)
        add_filter("target_id", target_id)
        add_filter("actor", actor)
        add_filter("client_id", client_id)
        add_filter("site_id", site_id)
        add_filter("building_id", building_id)
        add_filter("floor_id", floor_id)
        add_filter("room_id", room_id)
        add_filter("device_id", device_id)
        add_filter("gateway_id", gateway_id)
        add_filter("user_id", user_id)

        if search:
            where_clauses.append("""
            (
                action LIKE ?
                OR target_type LIKE ?
                OR target_id LIKE ?
                OR actor LIKE ?
                OR message LIKE ?
                OR details LIKE ?
            )
        """)

            search_value = f"%{search}%"

            params.extend([
                search_value,
                search_value,
                search_value,
                search_value,
                search_value,
                search_value
            ])

        if from_date:
            where_clauses.append("created_at >= ?")
            params.append(from_date)

        if to_date:
            where_clauses.append("created_at <= ?")

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
        FROM audit_log
        {where_sql}
        ORDER BY datetime(created_at) DESC, id DESC
        LIMIT ?
    """, params + [limit]).fetchall()

        output = io.StringIO()

        fieldnames = [
            "id",
            "timestamp",
            "actor",
            "action",
            "target_type",
            "target_id",
            "message",
            "client_id",
            "site_id",
            "building_id",
            "floor_id",
            "room_id",
            "device_id",
            "gateway_id",
            "user_id",
            "details"
        ]

        writer = SafeDictWriter(output, fieldnames=fieldnames)
        writer.writeheader()

        for row in rows:
            item = dict(row)

            try:
                details_obj = json.loads(item["details"]) if item.get("details") else {}
                details_obj = redact_secrets(details_obj)
                details_text = json.dumps(details_obj, ensure_ascii=False)
            except Exception:
                details_obj = {}
                details_text = item.get("details") or ""

            message = item.get("message")

            if not message:
                message = build_audit_message(
                    action=item.get("action"),
                    actor=item.get("actor"),
                    target_type=item.get("target_type"),
                    target_id=item.get("target_id"),
                    details=details_obj
                )

            writer.writerow({
                "id": item.get("id"),
                "timestamp": item.get("created_at"),
                "actor": item.get("actor"),
                "action": item.get("action"),
                "target_type": item.get("target_type"),
                "target_id": item.get("target_id"),
                "message": message,
                "client_id": item.get("client_id"),
                "site_id": item.get("site_id"),
                "building_id": item.get("building_id"),
                "floor_id": item.get("floor_id"),
                "room_id": item.get("room_id"),
                "device_id": item.get("device_id"),
                "gateway_id": item.get("gateway_id"),
                "user_id": item.get("user_id"),
                "details": details_text
            })

        conn.close()

        output.seek(0)

        export_date = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"audit_log_export_{export_date}.csv"

        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename={filename}"
            }
        )
    finally:
        conn.close()
