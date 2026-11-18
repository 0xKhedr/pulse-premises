"""Durable, explicit gateway admission and retry-safe TTN registration."""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone

import requests
from fastapi import HTTPException

from app.config import settings
from app.services.ttn import ttn_client


def candidate_by_id(conn, candidate_id: int) -> dict:
    row = conn.execute("SELECT * FROM gateway_approval_candidates WHERE id=?", (candidate_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Gateway attempt not found")
    return dict(row)


def public_candidate(candidate: dict) -> dict:
    return {key: value for key, value in candidate.items() if key != "approval_token"}


def assert_registration_matches(candidate: dict, gateway: dict | None) -> None:
    if (not gateway or gateway.get("ids", {}).get("eui", "").upper() != candidate["eui"]
            or gateway.get("attributes", {}).get("sbms-approval") != candidate["approval_token"]):
        raise HTTPException(409, "Gateway ID does not match this SBMS approval in TTN. Check its registration in the TTN Console.")
    if (gateway.get("frequency_plan_ids") != [candidate["frequency_plan_id"]]
            or bool(gateway.get("require_authenticated_connection", False)) != bool(candidate["require_authenticated_connection"])):
        raise HTTPException(409, "Gateway radio or authentication settings changed in TTN. Check its registration in the TTN Console.")


def check_enabled() -> None:
    if settings.PREVIEW_MODE:
        raise HTTPException(409, "Gateway admission is unavailable in preview mode")
    if not settings.TTN_GATEWAY_MANAGEMENT_API_KEY.strip():
        raise HTTPException(409, "Configure TTN_GATEWAY_MANAGEMENT_API_KEY before accepting gateways")


def upstream_error(exc: Exception) -> str:
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        code = exc.response.status_code
        if code in {401, 403}:
            return "TTN refused management access. Check the gateway management key and its rights."
        if code == 409:
            return "The gateway ID or EUI is already registered in TTN. Check it in the TTN Console."
        if code == 400:
            return "TTN rejected the gateway settings. Check its ID, EUI, frequency plan and owner."
        return f"TTN returned HTTP {code}. Check the TTN server before retrying."
    if isinstance(exc, requests.RequestException):
        return "TTN could not confirm registration. Retry with the same settings to reconcile the result."
    return "Gateway registration could not be completed. Check the server configuration and retry."


def _registration_options(conn, candidate: dict, data: dict) -> dict:
    if not candidate["eui"]:
        raise HTTPException(409, "This attempt has no gateway EUI; register it manually in the TTN Console")
    gateway_id = str(data.get("gateway_id") or candidate["gateway_id"] or f"gw-{candidate['eui'].lower()}").strip()
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{1,34}[a-z0-9])", gateway_id):
        raise HTTPException(400, "Gateway ID must be 3–36 lowercase letters, digits or hyphens, without edge hyphens")
    name = str(data.get("name") or candidate["name"] or gateway_id).strip()
    plan = str(data.get("frequency_plan_id") or candidate["frequency_plan_id"] or settings.LORAWAN_FREQUENCY_PLAN_ID).strip()
    if len(name) > 100 or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", plan):
        raise HTTPException(400, "Use a name up to 100 characters and a valid TTN frequency plan ID")
    authenticated = data.get("require_authenticated_connection")
    if authenticated is None:
        authenticated = candidate["require_authenticated_connection"]
    if authenticated is None:
        authenticated = candidate["protocol"] != "udp"
    if not isinstance(authenticated, (bool, int)) or authenticated not in (0, 1):
        raise HTTPException(400, "Authenticated connection must be true or false")
    if candidate["protocol"] == "basic_station" and not authenticated:
        raise HTTPException(400, "Basics Station gateways require an authenticated connection")
    site_id = data.get("site_id", candidate["site_id"])
    if site_id is not None:
        if isinstance(site_id, bool) or not isinstance(site_id, int):
            raise HTTPException(400, "Site ID must be a whole number")
        if not conn.execute("SELECT id FROM sites WHERE id=?", (site_id,)).fetchone():
            raise HTTPException(400, "Site not found")
    options = {"gateway_id": gateway_id, "name": name, "frequency_plan_id": plan,
               "require_authenticated_connection": int(authenticated), "site_id": site_id}
    # A timed-out create may already have succeeded. Keep the same identity
    # and radio settings on retries so we never register a second gateway.
    if candidate["gateway_id"] and any(
        options[field] != candidate[field]
        for field in ("gateway_id", "frequency_plan_id", "require_authenticated_connection")
    ):
        raise HTTPException(409, "Retry registration with the original gateway ID and radio settings")
    return options


def accept_candidate(conn, candidate_id: int, data: dict, actor: str) -> dict:
    check_enabled()
    conn.execute("BEGIN IMMEDIATE")
    try:
        candidate = candidate_by_id(conn, candidate_id)
        previous_registration = bool(candidate["gateway_id"])
        if candidate["status"] == "accepted":
            conn.rollback()
            return public_candidate(candidate)
        if candidate["status"] == "registering" and time.time() - candidate["registration_started_at"] < 120:
            raise HTTPException(409, "Registration is already in progress")
        options = _registration_options(conn, candidate, data)
        # Never overwrite a gateway already placed or managed in SBMS.
        if conn.execute("SELECT id FROM gateways WHERE gateway_id=?", (options["gateway_id"],)).fetchone():
            raise HTTPException(409, "This gateway ID already exists in SBMS; manage it from its existing record")
        conn.execute("""
            UPDATE gateway_approval_candidates SET status='registering', gateway_id=?, name=?,
                frequency_plan_id=?, require_authenticated_connection=?, site_id=?,
                decided_by=?, decided_at=?, registration_started_at=?, registration_error=NULL WHERE id=?
        """, (options["gateway_id"], options["name"], options["frequency_plan_id"],
              options["require_authenticated_connection"], options["site_id"], actor,
              datetime.now(timezone.utc).isoformat(), time.time(), candidate_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    candidate = candidate_by_id(conn, candidate_id)
    could_have_created = False
    try:
        gateway = ttn_client.get_managed_gateway(candidate["gateway_id"])
        if gateway is None:
            could_have_created = True
            try:
                gateway = ttn_client.register_gateway(candidate)
            except requests.HTTPError as exc:
                if exc.response is not None and exc.response.status_code in {400, 401, 403, 404, 409, 422}:
                    could_have_created = False
                raise
        # Reconcile only our own previous create, never adopt an existing TTN identity.
        assert_registration_matches(candidate, gateway)
        conn.execute("BEGIN IMMEDIATE")
        site = conn.execute("SELECT client_id FROM sites WHERE id=?", (candidate["site_id"],)).fetchone()
        conn.execute("""
            INSERT INTO gateways(gateway_id,name,client_id,site_id,status)
            VALUES (?,?,?,?,'offline')
        """, (candidate["gateway_id"], candidate["name"], site["client_id"] if site else None, candidate["site_id"]))
        conn.execute("""
            UPDATE gateway_approval_candidates SET status='accepted', registration_error=NULL WHERE id=?
        """, (candidate_id,))
        conn.commit()
    except Exception as exc:
        conn.rollback()
        message = exc.detail if isinstance(exc, HTTPException) else upstream_error(exc)
        conn.execute("""
            UPDATE gateway_approval_candidates SET status='registration_failed', registration_error=?,
                gateway_id=CASE WHEN ? THEN gateway_id ELSE NULL END WHERE id=?
        """, (message, previous_registration or could_have_created, candidate_id))
        conn.commit()
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(502, message)
    return public_candidate(candidate_by_id(conn, candidate_id))


def reject_candidate(conn, candidate_id: int, actor: str) -> dict:
    if settings.PREVIEW_MODE:
        raise HTTPException(409, "Gateway admission is unavailable in preview mode")
    conn.execute("BEGIN IMMEDIATE")
    try:
        candidate = candidate_by_id(conn, candidate_id)
        if candidate["status"] not in {"pending", "rejected"}:
            raise HTTPException(409, "Only unregistered pending attempts can be rejected")
        conn.execute("""
            UPDATE gateway_approval_candidates SET status='rejected', decided_by=?, decided_at=? WHERE id=?
        """, (actor, datetime.now(timezone.utc).isoformat(), candidate_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return public_candidate(candidate_by_id(conn, candidate_id))
