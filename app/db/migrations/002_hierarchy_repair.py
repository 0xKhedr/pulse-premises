from __future__ import annotations

import sqlite3


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not table_exists(conn, table):
        return set()
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def add_column(conn: sqlite3.Connection, table: str, definition: str) -> None:
    if not table_exists(conn, table):
        return
    name = definition.strip().split()[0].strip('"`[]')
    if name not in columns(conn, table):
        conn.execute(f'ALTER TABLE "{table}" ADD COLUMN {definition}')


def first_scope(conn: sqlite3.Connection) -> tuple[int, int]:
    client = conn.execute("SELECT id FROM clients ORDER BY id LIMIT 1").fetchone()
    if client:
        client_id = client["id"]
    else:
        client_id = conn.execute(
            "INSERT INTO clients(name) VALUES ('Legacy Client')"
        ).lastrowid

    site = conn.execute(
        "SELECT id FROM sites WHERE client_id=? ORDER BY id LIMIT 1", (client_id,)
    ).fetchone()
    if site:
        site_id = site["id"]
    else:
        site_id = conn.execute(
            "INSERT INTO sites(client_id, name) VALUES (?, 'Legacy Site')",
            (client_id,),
        ).lastrowid
    return client_id, site_id


def get_or_create_building(conn, site_id: int, name: str) -> int:
    row = conn.execute(
        "SELECT id FROM buildings WHERE site_id=? AND name=? LIMIT 1",
        (site_id, name),
    ).fetchone()
    if row:
        return row["id"]
    return conn.execute(
        "INSERT INTO buildings(site_id, name) VALUES (?, ?)", (site_id, name)
    ).lastrowid


def get_or_create_floor(
    conn,
    building_id: int,
    name: str,
    image_path=None,
    image_width=None,
    image_height=None,
    created_at=None,
) -> int:
    row = conn.execute(
        "SELECT id FROM floors WHERE building_id=? AND name=? LIMIT 1",
        (building_id, name),
    ).fetchone()
    if row:
        return row["id"]
    return conn.execute(
        """
        INSERT INTO floors(
            building_id, name, floor_number,
            image_path, image_width, image_height, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))
        """,
        (
            building_id,
            name,
            name,
            image_path,
            image_width,
            image_height,
            created_at,
        ),
    ).lastrowid


def up(conn: sqlite3.Connection) -> None:
    # Non-destructive compatibility backfill. No DROP TABLE operations.
    if not all(
        table_exists(conn, t) for t in ("clients", "sites", "buildings", "floors")
    ):
        return

    client_id, site_id = first_scope(conn)

    if table_exists(conn, "floorplans"):
        add_column(conn, "floorplans", "floor_id INTEGER")
        fp_cols = columns(conn, "floorplans")
        if {"building", "floor"}.issubset(fp_cols):
            for row in conn.execute("SELECT * FROM floorplans").fetchall():
                item = dict(row)
                bname = str(item.get("building") or "Legacy Building").strip()
                fname = str(item.get("floor") or "Legacy Floor").strip()
                bid = get_or_create_building(conn, site_id, bname)
                fid = get_or_create_floor(
                    conn,
                    bid,
                    fname,
                    item.get("image_path"),
                    item.get("image_width"),
                    item.get("image_height"),
                    item.get("created_at"),
                )
                conn.execute(
                    "UPDATE floorplans SET floor_id=? WHERE id=?", (fid, item["id"])
                )

    if table_exists(conn, "rooms"):
        add_column(conn, "rooms", "floor_id INTEGER")
        room_cols = columns(conn, "rooms")
        for row in conn.execute("SELECT * FROM rooms").fetchall():
            item = dict(row)
            if item.get("floor_id"):
                continue
            fid = None
            if "floorplan_id" in room_cols and item.get("floorplan_id"):
                fp = conn.execute(
                    "SELECT floor_id FROM floorplans WHERE id=?", (item["floorplan_id"],)
                ).fetchone()
                if fp and fp["floor_id"]:
                    fid = fp["floor_id"]
            if (
                fid is None
                and {"building", "floor"}.issubset(room_cols)
                and item.get("building")
                and item.get("floor")
            ):
                bid = get_or_create_building(conn, site_id, str(item["building"]).strip())
                fid = get_or_create_floor(conn, bid, str(item["floor"]).strip())
            if fid is not None:
                conn.execute("UPDATE rooms SET floor_id=? WHERE id=?", (fid, item["id"]))

    if table_exists(conn, "devices"):
        for definition in (
            "client_id INTEGER",
            "site_id INTEGER",
            "building_id INTEGER",
            "floor_id INTEGER",
            "room_id INTEGER",
        ):
            add_column(conn, "devices", definition)

        device_cols = columns(conn, "devices")
        room_cols = columns(conn, "rooms") if table_exists(conn, "rooms") else set()

        for row in conn.execute("SELECT * FROM devices").fetchall():
            item = dict(row)
            room_id = item.get("room_id")
            if (
                not room_id
                and {"building", "floor", "room"}.issubset(device_cols)
                and {"building", "floor", "room_name"}.issubset(room_cols)
            ):
                match = conn.execute(
                    """
                    SELECT id FROM rooms
                    WHERE building=? AND floor=? AND room_name=?
                    LIMIT 1
                    """,
                    (item.get("building"), item.get("floor"), item.get("room")),
                ).fetchone()
                if match:
                    room_id = match["id"]
            if not room_id:
                continue

            scope = conn.execute(
                """
                SELECT r.id AS room_id, f.id AS floor_id, b.id AS building_id,
                       s.id AS site_id, c.id AS client_id
                FROM rooms r
                JOIN floors f ON f.id=r.floor_id
                JOIN buildings b ON b.id=f.building_id
                JOIN sites s ON s.id=b.site_id
                JOIN clients c ON c.id=s.client_id
                WHERE r.id=? LIMIT 1
                """,
                (room_id,),
            ).fetchone()
            if scope:
                conn.execute(
                    """
                    UPDATE devices
                    SET client_id=?, site_id=?, building_id=?, floor_id=?, room_id=?
                    WHERE chip_mac=?
                    """,
                    (
                        scope["client_id"],
                        scope["site_id"],
                        scope["building_id"],
                        scope["floor_id"],
                        scope["room_id"],
                        item["chip_mac"],
                    ),
                )
