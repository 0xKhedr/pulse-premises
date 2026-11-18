"""Ingest the bounded, read-only TTN journal without access to the Docker socket.

TTN 3.36 UDP logs use namespace=gatewayserver/io/udp, gateway_eui and
msg=Failed to connect. Basics Station logs use gatewayserver/io/semtechws.
Only admission failures are retained, never raw logs or credentials.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings
from app.db.connection import get_db_connection

logger = logging.getLogger(__name__)
_health = {"last_checked_at": None, "error": None}


def parse_failure(entry: dict) -> dict | None:
    namespace = str(entry.get("namespace", ""))
    # HTTP middleware also logs failures returned before a connection is made.
    path = str(entry.get("http.path", entry.get("path", "")))
    is_station = "semtechws" in namespace or "/traffic/" in path or path == "/router-info"
    if namespace != "gatewayserver/io/udp" and not is_station:
        return None
    error = str(entry.get("error", "")).lower()
    if any(code in error for code in ("gateway_not_found", "gateway_eui_not_found", "not_registered")):
        reason = "Gateway is not registered in TTN"
    elif any(code in error for code in (
        "unauthenticated", "no_auth_provided", "invalid_authorization", "invalid_api_key",
        "permission_denied", "require_authenticated", "not_authorized", "no_gateway_rights",
    )):
        reason = "TTN refused gateway authentication or access"
    else:
        return None
    # Never extract identifiers from arbitrary error text or persist the error itself.
    eui = str(entry.get("gateway_eui", "")).replace(":", "").replace("-", "").upper()
    if not re.fullmatch(r"[0-9A-F]{16}", eui):
        match = re.search(r"eui-([0-9a-fA-F]{16})(?:/|$)", path)
        eui = match[1].upper() if match else ""
    gateway_id = str(entry.get("gateway_uid", "")).split("@", 1)[0]
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{1,34}[a-z0-9])", gateway_id):
        gateway_id = ""
    address = str(entry.get("remote_addr", entry.get("http.remote_addr", "")))
    source_ip = None
    for value in (address, address.rsplit(":", 1)[0].strip("[]")):
        try:
            source_ip = str(ipaddress.ip_address(value))
            break
        except ValueError:
            continue
    if not eui and not gateway_id:
        return None
    return {
        "identity": f"eui:{eui}" if eui else f"id:{gateway_id}",
        "eui": eui or None, "observed_gateway_id": gateway_id or None,
        "protocol": "basic_station" if is_station else "udp",
        "source_ip": source_ip, "reason": reason,
    }


def record_failure(conn, failure: dict, seen_at: str) -> None:
    existing = conn.execute(
        "SELECT id FROM gateway_approval_candidates WHERE identity=?", (failure["identity"],)
    ).fetchone()
    if existing:
        conn.execute("""
            UPDATE gateway_approval_candidates SET
                last_seen_at=MAX(last_seen_at, ?), seen_count=seen_count+1,
                source_ip=COALESCE(?, source_ip), reason=?, protocol=?
            WHERE id=?
        """, (seen_at, failure["source_ip"], failure["reason"], failure["protocol"], existing["id"]))
        return
    count = conn.execute("SELECT COUNT(*) FROM gateway_approval_candidates").fetchone()[0]
    if count >= settings.GATEWAY_APPROVAL_MAX_CANDIDATES:
        _health["error"] = "Gateway approval inbox is full; new identities are being skipped."
        return
    conn.execute("""
        INSERT INTO gateway_approval_candidates(
            identity,eui,observed_gateway_id,protocol,source_ip,reason,
            first_seen_at,last_seen_at,approval_token)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (failure["identity"], failure["eui"], failure["observed_gateway_id"],
          failure["protocol"], failure["source_ip"], failure["reason"], seen_at, seen_at, uuid.uuid4().hex))


def _journal_lines(path: Path):
    try:
        with path.open("rb") as stream:
            # Bound each pass by bytes, rather than by the number of records:
            # short lines must not permanently hide the tail of a journal.
            remaining = 2 * 1024 * 1024
            while remaining > 0:
                line = stream.readline(min(65537, remaining))
                if not line:
                    break
                remaining -= len(line)
                yield line
    except FileNotFoundError:
        return


def collect_once() -> None:
    directory = settings.TTN_GATEWAY_ATTEMPT_LOG_DIR
    if not directory or settings.PREVIEW_MODE:
        return
    conn = get_db_connection()
    try:
        # Rotation is previous -> current; durable sequence cursors make rereads
        # and concurrent app workers harmless, including after an app restart.
        conn.execute("BEGIN IMMEDIATE")
        for name in ("attempts.previous", "attempts.current"):
            for line in _journal_lines(Path(directory) / name):
                if len(line) > 65536 or not line.endswith(b"\n"):
                    continue
                try:
                    boot, seq, epoch, payload = line.decode("utf-8").split("\t", 3)
                    if not re.fullmatch(r"[a-zA-Z0-9-]{1,80}", boot):
                        continue
                    sequence = int(seq)
                    if sequence < 1:
                        continue
                    seen_at = datetime.fromtimestamp(float(epoch), timezone.utc).isoformat()
                    entry = json.loads(payload)
                    if not isinstance(entry, dict):
                        continue
                except (ValueError, OverflowError, OSError):
                    continue
                cursor = conn.execute(
                    "SELECT sequence FROM gateway_attempt_cursors WHERE boot_id=?", (boot,)
                ).fetchone()
                if cursor and sequence <= cursor[0]:
                    continue
                failure = parse_failure(entry)
                if failure:
                    record_failure(conn, failure, seen_at)
                conn.execute("""
                    INSERT INTO gateway_attempt_cursors(boot_id,sequence) VALUES (?,?)
                    ON CONFLICT(boot_id) DO UPDATE SET sequence=excluded.sequence
                """, (boot, sequence))
        count = conn.execute("SELECT COUNT(*) FROM gateway_approval_candidates").fetchone()[0]
        _health["error"] = ("Gateway approval inbox is full; new identities are being skipped."
                            if count >= settings.GATEWAY_APPROVAL_MAX_CANDIDATES else None)
        conn.commit()
        _health["last_checked_at"] = datetime.now(timezone.utc).isoformat()
    finally:
        conn.close()


def collector_status() -> dict:
    enabled = bool(settings.TTN_GATEWAY_ATTEMPT_LOG_DIR) and not settings.PREVIEW_MODE
    active = False
    if enabled:
        try:
            active = time.time() - (Path(settings.TTN_GATEWAY_ATTEMPT_LOG_DIR) / "heartbeat").stat().st_mtime < 45
        except OSError:
            pass
    return {"enabled": enabled, "active": active, **_health}


async def run_collector() -> None:
    while True:
        try:
            await asyncio.to_thread(collect_once)
        except Exception:
            logger.exception("Gateway attempt collection failed")
            _health["error"] = "Cannot read the TTN gateway journal. Check the stack and shared log volume."
        await asyncio.sleep(3)
