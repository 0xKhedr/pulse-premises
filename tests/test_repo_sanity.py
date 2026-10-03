from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_python_files_parse():
    for path in (ROOT / "app").rglob("*.py"):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_run_scripts_use_current_entrypoint():
    assert "app.main:app" in (ROOT / "linux/run.sh").read_text(encoding="utf-8")
    assert "app.main:app" in (ROOT / "windows/run.bat").read_text(encoding="utf-8")


def test_missing_dependency_fixed():
    assert "pydantic-settings" in (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()


def test_runtime_dependencies_are_pinned():
    lines = [
        line.strip()
        for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert lines
    assert all("==" in line for line in lines)


def test_firmware_uses_generated_shared_credentials():
    firmware = (
        ROOT / "firmware/lilygo_cayenne_lpp_node/lilygo_cayenne_lpp_node.ino"
    ).read_text(
        encoding="utf-8"
    )
    assert '#include "lorawan_credentials.h"' in firmware
    assert "SBMS_DEV_EUI_XOR_LSB_BYTES" in firmware
    assert "derive_dev_eui_from_mac" in (
        ROOT / "app/services/lorawan_identity.py"
    ).read_text(encoding="utf-8")


def test_operational_run_scripts_do_not_force_reload():
    windows = (ROOT / "windows/run.bat").read_text(encoding="utf-8")
    linux = (ROOT / "linux/run.sh").read_text(encoding="utf-8")
    assert '"%SBMS_RELOAD%"=="1"' in windows
    assert '"${SBMS_RELOAD:-0}" = "1"' in linux


def test_debug_route_removed():
    text = "".join([p.read_text(encoding="utf-8") for p in (ROOT / "app/routers").glob("*.py")])
    assert '@router.get("/test-token-refresh")' not in text


def test_credentials_route_unique():
    count = 0
    for path in (ROOT / "app/routers").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        count += text.count('@router.put("/users/{user_id}/credentials")')
    assert count == 1


def test_service_worker_scope():
    text = (ROOT / "app/routers/pages.py").read_text(encoding="utf-8")
    assert "register('/uploads/service-worker.js')" not in text
    assert 'register("/uploads/service-worker.js")' not in text


def test_no_old_alarm_timestamp_query():
    text = "".join([p.read_text(encoding="utf-8") for p in (ROOT / "app/routers").glob("*.py")])
    assert "alarm_history WHERE timestamp" not in text


def test_no_duplicate_routes_within_router_file():
    pattern = re.compile(
        r'@(?:app|router)\.(get|post|put|delete|patch)\(\s*["\']([^"\']+)["\']'
    )
    for path in (ROOT / "app").rglob("*.py"):
        seen = set()
        for method, route in pattern.findall(path.read_text(encoding="utf-8")):
            key = (method.upper(), route)
            assert key not in seen, f"duplicate {key} in {path}"
            seen.add(key)
