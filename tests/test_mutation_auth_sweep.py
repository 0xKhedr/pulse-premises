from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from tests.route_inventory import (
    fill_path,
    is_public,
    mutating_routes,
    snapshot_state,
)

# The only state-changing endpoints that are reachable without a session.
# /auth/login issues sessions; /ttn-webhook is called by TTN itself and is
# gated by its own shared secret instead (see test_webhook_security.py).
PUBLIC_MUTATIONS = {
    ("POST", "/auth/login"),
    ("POST", "/ttn-webhook"),
}


@pytest.mark.parametrize("verb,path", mutating_routes())
def test_mutating_route_rejects_anonymous(verb: str, path: str, client: TestClient):
    """
    Every endpoint that can change state must refuse an anonymous caller.

    The existing route sweep only covered GET, so the 68 POST/PUT/DELETE
    endpoints -- including the cascading ``DELETE /clients/{id}`` -- had no
    automated guarantee that they are behind the session gate at all. This
    closes that half, and covers any mutating route added later for free.
    """
    if (verb, path) in PUBLIC_MUTATIONS or is_public(path):
        pytest.skip("intentionally public endpoint")

    response = client.request(
        verb, fill_path(path), json={}, follow_redirects=False
    )

    assert response.status_code in (302, 401, 403), (
        f"{verb} {path} served an anonymous caller with "
        f"{response.status_code}: {response.text[:300]}"
    )


def test_anonymous_mutation_sweep_changes_no_data(
    client: TestClient, db_conn: sqlite3.Connection
):
    """
    Calling every mutating endpoint anonymously must leave the database
    byte-for-byte unchanged.

    Status codes alone are not proof: a handler could perform its write and
    only then redirect. This asserts on the data itself, so a route that
    mutates before checking the session is caught even if it returns 302.
    """
    before = snapshot_state(db_conn)

    for verb, path in mutating_routes():
        if (verb, path) in PUBLIC_MUTATIONS or is_public(path):
            continue
        client.request(verb, fill_path(path), json={}, follow_redirects=False)

    after = snapshot_state(db_conn)

    drifted = {
        table: (before[table], after[table])
        for table in before
        if before[table] != after.get(table)
    }
    assert not drifted, (
        "anonymous requests changed persisted data "
        f"(table: before -> after): {drifted}"
    )
