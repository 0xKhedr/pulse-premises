import json

import pytest
import requests

from app.config import settings
from app.services.ttn import TTNClient


@pytest.fixture
def ttn(monkeypatch):
    monkeypatch.setattr(settings, "PREVIEW_MODE", False)
    monkeypatch.setattr(settings, "TTN_BASE_URL", "http://stack:1885")
    monkeypatch.setattr(settings, "TTN_API_KEY", "application-key")
    monkeypatch.setattr(settings, "AUTO_ENROLLMENT_GATEWAY_API_KEY", "gateway-key")
    return TTNClient()


def response(status, body):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(body).encode()
    return result


@pytest.mark.parametrize("gateway_key,expected", [
    ("gateway-key", "gateway-key"),
    ("", "application-key"),
    ("   ", "application-key"),
])
def test_gateway_reads_use_gateway_key_and_application_reads_keep_their_key(
    ttn, monkeypatch, gateway_key, expected
):
    monkeypatch.setattr(settings, "AUTO_ENROLLMENT_GATEWAY_API_KEY", gateway_key)
    calls = []

    def request(method, url, **kwargs):
        calls.append((url, kwargs["headers"]["Authorization"]))
        return response(200, {"connected_at": "2026-11-03T12:00:00Z"})

    monkeypatch.setattr("app.services.ttn.requests.request", request)
    ttn.get_gateway_info("test-gateway")
    ttn.get_gateway_stats("test-gateway")
    ttn.get_application_device("test-node")
    assert [auth for _, auth in calls] == [
        f"Bearer {expected}", f"Bearer {expected}", "Bearer application-key",
    ]


def test_disconnected_gateway_is_offline(ttn, monkeypatch):
    monkeypatch.setattr("app.services.ttn.requests.request", lambda *a, **k: response(404, {
        "code": 5,
        "details": [{"namespace": "pkg/gatewayserver", "name": "not_connected"}],
    }))
    assert ttn.get_gateway_stats("test-gateway") == {}


@pytest.mark.parametrize("status,body", [
    (400, {"message": "invalid token"}),
    (403, {"message": "permission denied"}),
    (404, {"details": [{"namespace": "pkg/identityserver", "name": "gateway_not_found"}]}),
])
def test_authentication_and_missing_gateway_errors_are_not_reported_as_offline(
    ttn, monkeypatch, status, body
):
    calls = []

    def request(*args, **kwargs):
        calls.append(kwargs["headers"]["Authorization"])
        return response(status, body)

    monkeypatch.setattr("app.services.ttn.requests.request", request)
    with pytest.raises(requests.HTTPError):
        ttn.get_gateway_stats("test-gateway")
    assert calls == ["Bearer gateway-key"]
