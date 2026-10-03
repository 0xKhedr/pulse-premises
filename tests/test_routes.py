from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


def test_no_duplicate_route_paths_and_methods():
    seen = set()
    duplicates = []
    for route in app.routes:
        methods = getattr(route, "methods", None)
        path = getattr(route, "path", None)
        if methods and path:
            for method in methods:
                key = (method, path)
                if key in seen:
                    duplicates.append(key)
                seen.add(key)

    assert not duplicates, f"Duplicate routes found in FastAPI app: {duplicates}"


def test_static_files_route_mounted(client: TestClient):
    mount_paths = [getattr(r, "path", None) for r in app.routes]
    assert "/static" in mount_paths or "/static/{path:path}" in mount_paths or any(
        p and p.startswith("/static") for p in mount_paths
    )
    # Smoke test static file serving
    res = client.get("/static/manifest.json")
    assert res.status_code == 200


def test_service_worker_route_exists(client: TestClient):
    paths = [getattr(r, "path", None) for r in app.routes]
    assert "/service-worker.js" in paths

    response = client.get("/service-worker.js")
    assert response.status_code == 200
    assert "javascript" in response.headers.get("content-type", "").lower()


def test_healthz_checks_database_and_is_public(client: TestClient):
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_security_headers_are_present(client: TestClient):
    response = client.get("/")

    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"


def test_node_placement_ui_is_floor_only():
    html = Path("templates/node_placement_editor.html").read_text(encoding="utf-8")

    assert "Site / Campus Photo View" not in html
    assert 'id="modalSiteSelect"' not in html
    assert 'id="modalBuildingSelect"' not in html
