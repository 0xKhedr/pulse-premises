"""
The address we connect to is not always the address the cluster calls itself.

On TTN's public cloud the two coincide, which is why the client derived the
Join/Network/Application server addresses straight from TTN_BASE_URL. A
self-hosted stack breaks that assumption: reached from a container at
host.docker.internal:1885 while identifying its own servers as ``localhost``,
it rejects every device registration with network_server_address_mismatch.
"""

from __future__ import annotations

import pytest

from app.config import settings
from app.services.ttn import TTNClient


@pytest.mark.parametrize(
    "base_url, expected",
    [
        ("https://eu1.cloud.thethings.network", "eu1.cloud.thethings.network"),
        ("http://localhost:1885", "localhost:1885"),
        ("http://host.docker.internal:1885/", "host.docker.internal:1885"),
    ],
)
def test_cluster_address_defaults_to_the_api_host(base_url, expected, monkeypatch):
    """Unset, behaviour is unchanged: the cluster address is the API host."""
    monkeypatch.setattr(settings, "TTN_BASE_URL", base_url)
    monkeypatch.setattr(settings, "TTN_CLUSTER_ADDRESS", "")

    assert TTNClient().host == expected


def test_cluster_address_overrides_the_api_host(monkeypatch):
    """
    The exact case observed against a local Things Stack: the container talks
    to host.docker.internal, but devices must be registered against the name
    the stack knows itself by, or registration fails with a 400.
    """
    monkeypatch.setattr(settings, "TTN_BASE_URL", "http://host.docker.internal:1885")
    monkeypatch.setattr(settings, "TTN_CLUSTER_ADDRESS", "localhost")

    client = TTNClient()
    assert client.base_url == "http://host.docker.internal:1885", "still connects here"
    assert client.host == "localhost", "but registers devices against this"


def test_cluster_address_ignores_surrounding_whitespace(monkeypatch):
    monkeypatch.setattr(settings, "TTN_BASE_URL", "http://host.docker.internal:1885")
    monkeypatch.setattr(settings, "TTN_CLUSTER_ADDRESS", "  localhost  ")

    assert TTNClient().host == "localhost"
