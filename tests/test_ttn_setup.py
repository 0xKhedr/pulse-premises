from __future__ import annotations

import copy
import json

import pytest
import requests

from app.config import settings
from app.services import ttn_setup
from app.services.ttn import TTNClient


@pytest.fixture
def stack(monkeypatch):
    monkeypatch.setattr(settings, "PREVIEW_MODE", False)
    monkeypatch.setattr(settings, "TTN_AUTO_SETUP_ENABLED", True)
    monkeypatch.setattr(settings, "TTN_API_KEY", "")
    monkeypatch.setattr(settings, "TTN_GATEWAY_MANAGEMENT_API_KEY", "backend-key")
    monkeypatch.setattr(settings, "TTN_BASE_URL", "http://stack:1885")
    monkeypatch.setattr(settings, "TTN_APP_ID", "building-test")
    monkeypatch.setattr(settings, "TTN_CLUSTER_ADDRESS", "localhost")
    monkeypatch.setattr(settings, "TTN_GATEWAY_OWNER_TYPE", "users")
    monkeypatch.setattr(settings, "TTN_GATEWAY_OWNER_ID", "admin")
    monkeypatch.setattr(settings, "TTN_WEBHOOK_BASE_URL", "http://sbms-app:8000")
    monkeypatch.setattr(settings, "TTN_WEBHOOK_ID", "sbms")
    monkeypatch.setattr(settings, "TTN_WEBHOOK_SECRET", "private-webhook-secret")
    monkeypatch.setattr(ttn_setup, "_state", {
        "application_ready": False, "webhook_ready": False, "webhook_id": None,
        "last_checked_at": None, "error": None,
    })
    client = TTNClient()
    monkeypatch.setattr(ttn_setup, "ttn_client", client)
    remote = {"application": None, "webhooks": {}, "calls": []}

    def request(method, url, **kwargs):
        remote["calls"].append((method, url, kwargs))
        assert kwargs["headers"]["Authorization"] == "Bearer backend-key"
        status, body = 200, {}
        if method == "GET" and url.endswith("/applications/building-test"):
            status = 200 if remote["application"] else 404
            body = remote["application"] or {}
        elif method == "POST" and url.endswith("/users/admin/applications"):
            remote["application"] = copy.deepcopy(kwargs["json"]["application"])
            body = remote["application"]
        elif method == "GET" and url.endswith("/as/webhooks/building-test"):
            body = {"webhooks": list(remote["webhooks"].values())}
        elif method == "PUT" and "/as/webhooks/building-test/" in url:
            hook = copy.deepcopy(kwargs["json"]["webhook"])
            webhook_id = hook["ids"]["webhook_id"]
            remote["webhooks"][webhook_id] = {**remote["webhooks"].get(webhook_id, {}), **hook}
        else:
            raise AssertionError(f"Unexpected request: {method} {url}")
        result = requests.Response()
        result.status_code = status
        result._content = json.dumps(body).encode()
        return result

    monkeypatch.setattr("app.services.ttn.requests.request", request)
    return remote


def test_fresh_start_creates_application_and_webhook_using_one_key(stack):
    assert ttn_setup.configure_once()
    assert stack["application"]["ids"]["application_id"] == "building-test"
    assert stack["application"]["network_server_address"] == "localhost"
    hook = stack["webhooks"]["sbms"]
    assert hook["headers"] == {"X-Webhook-Secret": "private-webhook-secret"}
    assert hook["uplink_message"] == {"path": "/ttn-webhook"}
    assert hook["base_url"] == "http://sbms-app:8000"
    assert ttn_setup.status()["application_ready"]
    assert ttn_setup.status()["webhook_ready"]
    assert "private-webhook-secret" not in json.dumps(ttn_setup.status())


def test_repeated_start_preserves_application_and_does_not_duplicate_webhooks(stack):
    assert ttn_setup.configure_once()
    stack["application"]["name"] = "Existing building name"
    stack["calls"].clear()
    assert ttn_setup.configure_once()
    assert stack["application"]["name"] == "Existing building name"
    assert len(stack["webhooks"]) == 1
    assert all(method == "GET" for method, _, _ in stack["calls"])


def test_reuses_manual_webhook_and_updates_secret_preserving_other_integrations(stack):
    stack["application"] = {"ids": {"application_id": "building-test"}}
    existing = {
        "ids": {"webhook_id": "ttn-webhook"}, "base_url": "http://sbms-app:8000",
        "format": "json", "headers": {"x-webhook-secret": "old-secret", "X-Custom": "preserved"},
        "uplink_message": {"path": "/ttn-webhook"}, "join_accept": {"path": "/join"},
    }
    unrelated = {"ids": {"webhook_id": "other-service"}, "base_url": "https://example.com"}
    stack["webhooks"] = {"ttn-webhook": existing, "other-service": copy.deepcopy(unrelated)}
    assert ttn_setup.configure_once()
    assert set(stack["webhooks"]) == {"ttn-webhook", "other-service"}
    assert stack["webhooks"]["other-service"] == unrelated
    hook = stack["webhooks"]["ttn-webhook"]
    assert hook["join_accept"] == {"path": "/join"}
    assert hook["headers"] == {"X-Custom": "preserved", "X-Webhook-Secret": "private-webhook-secret"}
    assert ttn_setup.status()["webhook_id"] == "ttn-webhook"


@pytest.mark.parametrize("setting,value", [
    ("PREVIEW_MODE", True), ("TTN_AUTO_SETUP_ENABLED", False),
])
def test_disabled_setup_never_calls_ttn(stack, monkeypatch, setting, value):
    monkeypatch.setattr(settings, setting, value)
    assert ttn_setup.configure_once()
    assert stack["calls"] == []


def test_no_external_callback_url_creates_only_the_application(stack, monkeypatch):
    monkeypatch.setattr(settings, "TTN_WEBHOOK_BASE_URL", "")
    assert ttn_setup.configure_once()
    assert stack["application"] is not None
    assert stack["webhooks"] == {}
    assert not ttn_setup.status()["webhook_enabled"]


@pytest.mark.parametrize("setting,value", [
    ("TTN_WEBHOOK_SECRET", ""),
    ("TTN_WEBHOOK_BASE_URL", "http://user:password@example.com"),
    ("TTN_WEBHOOK_ID", "invalid/id"),
])
def test_invalid_callback_configuration_never_writes_a_webhook(stack, monkeypatch, setting, value):
    monkeypatch.setattr(settings, setting, value)
    assert not ttn_setup.configure_once()
    assert stack["webhooks"] == {}
    assert ttn_setup.status()["error"]
    assert "password" not in json.dumps(ttn_setup.status())


def test_permission_failure_reports_safe_status_and_retry_can_finish(stack, monkeypatch):
    original = ttn_setup.ttn_client.ensure_application

    def denied():
        response = requests.Response()
        response.status_code = 403
        raise requests.HTTPError("private-webhook-secret", response=response)

    monkeypatch.setattr(ttn_setup.ttn_client, "ensure_application", denied)
    assert not ttn_setup.configure_once()
    assert "rights" in ttn_setup.status()["error"]
    assert "private-webhook-secret" not in json.dumps(ttn_setup.status())
    assert stack["webhooks"] == {}
    monkeypatch.setattr(ttn_setup.ttn_client, "ensure_application", original)
    assert ttn_setup.configure_once()
    assert ttn_setup.status()["error"] is None


def test_webhook_validation_response_does_not_leak_secret_to_logs(stack, monkeypatch, caplog):
    def invalid(*args, **kwargs):
        result = requests.Response()
        result.status_code = 400
        result._content = b'private-webhook-secret'
        return result

    monkeypatch.setattr("app.services.ttn.requests.request", invalid)
    with pytest.raises(requests.HTTPError):
        ttn_setup.ttn_client.set_application_webhook({"ids": {"webhook_id": "sbms"}})
    assert "private-webhook-secret" not in caplog.text
