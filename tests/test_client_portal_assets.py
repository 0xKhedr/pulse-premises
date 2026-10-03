from pathlib import Path

from fastapi.testclient import TestClient

CLIENT_PORTAL_TEMPLATES = (
    Path("templates/client_portal_page.html"),
    Path("templates/client_login_page.html"),
)
SHARED_SCRIPT_TAG = '<script src="/static/client_portal.js"></script>'


def test_client_portal_templates_load_one_shared_script():
    for template_path in CLIENT_PORTAL_TEMPLATES:
        html = template_path.read_text(encoding="utf-8")

        assert html.count(SHARED_SCRIPT_TAG) == 1
        assert "<script>" not in html
        assert "function loadFloor(" not in html


def test_client_portal_shared_script_is_served(client: TestClient):
    response = client.get("/static/client_portal.js")

    assert response.status_code == 200
    assert response.headers.get("content-type", "").lower().split(";", 1)[0] in {
        "application/javascript",
        "text/javascript",
    }
    assert "async function loadFloor(" in response.text
    assert "function drawDevices(" in response.text
    assert "function exportClientAlarmHistoryCsv(" in response.text
