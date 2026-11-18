"""Ensure application and uplink integration exist without per-gateway setup."""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

import requests

from app.config import settings
from app.services.ttn import ttn_client

logger = logging.getLogger(__name__)
_state = {"application_ready": False, "webhook_ready": False,
          "webhook_id": None, "error": None, "last_checked_at": None}


def status() -> dict:
    return {"enabled": settings.TTN_AUTO_SETUP_ENABLED and not settings.PREVIEW_MODE,
            "webhook_enabled": bool(settings.TTN_WEBHOOK_BASE_URL.strip()), **_state}


def _endpoint(webhook: dict) -> str | None:
    if not isinstance(webhook.get("uplink_message"), dict):
        return None
    return (webhook.get("base_url", "").rstrip("/") + "/"
            + webhook["uplink_message"].get("path", "").lstrip("/")).rstrip("/")


def _ensure_webhook() -> str:
    base = settings.TTN_WEBHOOK_BASE_URL.strip().rstrip("/")
    url = urlsplit(base)
    if (url.scheme not in {"http", "https"} or not url.hostname or url.username
            or url.password or url.query or url.fragment):
        raise ValueError("TTN_WEBHOOK_BASE_URL must be an HTTP(S) address reachable from TTN")
    secret = settings.TTN_WEBHOOK_SECRET.strip()
    if not secret:
        raise ValueError("Set TTN_WEBHOOK_SECRET before automatic webhook setup")
    webhook_id = settings.TTN_WEBHOOK_ID.strip()
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{1,34}[a-z0-9])", webhook_id):
        raise ValueError("TTN_WEBHOOK_ID must be a valid 3–36 character webhook ID")
    hooks = ttn_client.list_application_webhooks()
    current = next((hook for hook in hooks if hook.get("ids", {}).get("webhook_id") == webhook_id), None)
    if current is None:
        # Reuse an existing manual integration rather than deliver each uplink twice.
        current = next((hook for hook in hooks if _endpoint(hook) == base + "/ttn-webhook"), None)
    current = current or {}
    webhook_id = current.get("ids", {}).get("webhook_id", webhook_id)
    headers = {key: value for key, value in current.get("headers", {}).items()
               if key.lower() != "x-webhook-secret"}
    headers["X-Webhook-Secret"] = secret
    desired = {
        "ids": {"application_ids": {"application_id": ttn_client.app_id}, "webhook_id": webhook_id},
        "base_url": base, "format": "json", "headers": headers,
        "uplink_message": {"path": "/ttn-webhook"}, "paused": False,
    }
    if any(current.get(key, False if key == "paused" else None) != value
           for key, value in desired.items() if key != "ids"):
        ttn_client.set_application_webhook(desired)
    return webhook_id


def configure_once() -> bool:
    if not status()["enabled"]:
        return True
    try:
        if not ttn_client.api_key:
            raise ValueError("Configure TTN_GATEWAY_MANAGEMENT_API_KEY for automatic application setup")
        ttn_client.ensure_application()
        _state["application_ready"] = True
        if settings.TTN_WEBHOOK_BASE_URL.strip():
            _state["webhook_ready"] = False
            _state["webhook_id"] = _ensure_webhook()
            _state["webhook_ready"] = True
        _state["error"] = None
        return True
    except ValueError as exc:
        _state["error"] = str(exc)
    except requests.HTTPError as exc:
        code = exc.response.status_code if exc.response is not None else "unknown"
        _state["error"] = (
            "TTN refused automatic setup. Check application creation, information and settings rights on the backend key."
            if code in {401, 403} else f"TTN automatic setup returned HTTP {code}; it will retry."
        )
    except Exception:
        # Upstream validation errors may contain webhook credentials; never expose them.
        _state["error"] = "TTN automatic setup could not finish; check TTN connectivity and backend key configuration."
    finally:
        _state["last_checked_at"] = datetime.now(timezone.utc).isoformat()
    logger.warning("%s", _state["error"])
    return False


async def run_setup() -> None:
    while not await asyncio.to_thread(configure_once):
        await asyncio.sleep(30)
