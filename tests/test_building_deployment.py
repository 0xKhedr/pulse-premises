from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import pytest

from tools.prepare_building import ROOT, prepare, stack_config, validate_host


def test_preparation_overwrites_existing_configuration(tmp_path):
    env_file, stack_file = prepare("192.168.10.20", "admin@building.local", tmp_path)
    assert env_file == tmp_path / ".env"
    env_bytes = env_file.read_bytes()
    config_bytes = stack_file.read_bytes()
    prepare("192.168.10.21", "admin@building.local", tmp_path)
    assert env_file.read_bytes() != env_bytes
    assert stack_file.read_bytes() != config_bytes
    values = dict(line.split("=", 1) for line in env_file.read_text().splitlines()
                  if line and not line.startswith("#"))
    assert values["TTS_HOST"] == "192.168.10.21"
    assert "replace-with" not in "\n".join(values.values())
    assert "SBMS_IMAGE" not in values
    assert len(bytes.fromhex(values["LORAWAN_APP_KEY"])) == 16
    assert len(bytes.fromhex(values["TTS_COOKIE_BLOCK_KEY"])) == 32
    assert len(bytes.fromhex(values["TTS_COOKIE_HASH_KEY"])) == 64
    assert len(bytes.fromhex(values["TTS_CLUSTER_KEY"])) == 16
    assert values["TTS_POSTGRES_PASSWORD"] == "root"
    assert values["TTS_CONSOLE_SECRET"] == "console"
    assert values["ADMIN_PASSWORD"] == "admin"
    assert values["AUTO_ENROLLMENT_ENABLED"] == "true"
    if os.name != "nt":
        assert env_file.stat().st_mode & 0o777 == 0o600


def test_preparation_overwrites_existing_env_and_creates_stack_config(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("LOCAL_TEST_MODE=true\n", encoding="utf-8")
    prepare(output_dir=tmp_path)
    assert "LOCAL_TEST_MODE=false\n" in env_file.read_text(encoding="utf-8")
    assert (tmp_path / "deploy/stack/ttn-lw-stack.yml").is_file()


@pytest.mark.parametrize("host", ["0.0.0.0", "224.1.1.1", "::1",
                                  "192.168.1.999", "http://building.lan", "building.lan:1885",
                                  "bad\nhost", "$(whoami)", "bad_host"])
def test_invalid_server_addresses_are_rejected(host):
    with pytest.raises(ValueError):
        validate_host(host)


@pytest.mark.parametrize(("args", "host", "email"), [
    ([], "localhost", "admin@localhost"),
    (["--host", "127.0.0.1"], "127.0.0.1", "admin@127.0.0.1"),
    (["--host", "pulse.building.lan"], "pulse.building.lan", "admin@pulse.building.lan"),
    (["--admin-email", "owner@example.com"], "localhost", "owner@example.com"),
    (["--host", "192.168.10.20", "--admin-email", "owner@example.com"],
     "192.168.10.20", "owner@example.com"),
])
def test_preparation_cli_defaults_and_overrides(tmp_path, args, host, email):
    subprocess.run(
        [sys.executable, str(ROOT / "tools/prepare_building.py"), "--output-dir", str(tmp_path), *args],
        capture_output=True, text=True, check=True,
    )
    values = dict(line.split("=", 1) for line in (tmp_path / ".env").read_text().splitlines()
                  if line and not line.startswith("#"))
    assert values["TTS_HOST"] == host
    assert values["ADMIN_EMAIL"] == email
    assert values["TTN_BASE_URL"] == f"http://{host}:1885"
    config = json.loads((tmp_path / "deploy/stack/ttn-lw-stack.yml").read_text())
    assert config["console"]["ui"]["canonical-url"] == f"http://{host}:1885/console"


@pytest.mark.parametrize("path", ["http", "grpc", "interop", "gs.mqtt", "gs.mqtt-v2",
                                  "gs.basic-station", "gs.ttigw", "as.mqtt"])
def test_lan_stack_disables_tls_listeners_without_certificates(path):
    options = stack_config("pulse.building.lan")
    for section in path.split("."):
        options = options[section]
    assert options["listen-tls"] == ""


def test_default_compose_builds_local_app_alongside_stack_dependencies(tmp_path):
    if not shutil.which("docker"):
        pytest.skip("Docker CLI is required to validate Compose")
    _, config_file = prepare("pulse.building.lan", "admin@building.local", tmp_path)
    shutil.copy2(ROOT / "docker-compose.yml", tmp_path)
    shutil.copy2(ROOT / "Dockerfile", tmp_path)
    (tmp_path / "deploy").mkdir(exist_ok=True)
    shutil.copy2(ROOT / "deploy/Dockerfile.stack", tmp_path / "deploy")
    shutil.copy2(ROOT / ".docker-local-ca.cer.example", tmp_path)
    result = subprocess.run(
        ["docker", "compose", "config", "--format", "json"],
        cwd=tmp_path, capture_output=True, text=True, check=True,
    )
    resolved = json.loads(result.stdout)
    services = resolved["services"]
    assert set(services) == {"sbms-app", "stack", "postgres", "redis"}
    app = services["sbms-app"]
    assert "image" not in app
    assert os.path.samefile(app["build"]["context"], tmp_path)
    assert app["build"]["dockerfile"] == "Dockerfile"
    assert app["build"]["secrets"] == [{"source": "sbms_local_ca"}]
    assert os.path.samefile(resolved["secrets"]["sbms_local_ca"]["file"],
                           tmp_path / ".docker-local-ca.cer.example")
    assert app["depends_on"]["stack"]["condition"] == "service_healthy"
    assert set(services["stack"]["depends_on"]) == {"postgres", "redis"}
    assert services["stack"]["build"]["dockerfile"] == "deploy/Dockerfile.stack"
    assert services["stack"]["environment"]["TTS_BOOTSTRAP_EMAIL"] == "admin@127.0.0.1"
    assert services["stack"]["environment"]["TTS_BOOTSTRAP_PASSWORD"] == "admin"
    assert app["environment"]["TTN_BASE_URL"] == "http://stack:1885"
    assert app["environment"]["THINGSBOARD_ENABLED"] == "false"
    assert app["environment"]["ADMIN_PASSWORD"] == "admin"
    assert app["environment"]["AUTO_ENROLLMENT_ENABLED"] == "true"
    assert services["postgres"]["environment"]["POSTGRES_PASSWORD"] == "root"
    assert services["stack"]["environment"]["TTN_LW_CONSOLE_OAUTH_CLIENT_SECRET"] == "console"
    assert services["stack"]["environment"]["TTN_LW_IS_DATABASE_URI"] == (
        "postgres://ttn:root@postgres:5432/ttn_lorawan?sslmode=disable"
    )
    assert all(mount["type"] == "volume" for mount in app["volumes"])
    assert not (ROOT / "docker-compose.override.yml").exists()
    assert not services["postgres"].get("ports")
    assert not services["redis"].get("ports")
    config = json.loads(config_file.read_text(encoding="utf-8"))
    assert config["console"]["ui"]["canonical-url"] == "http://pulse.building.lan:1885/console"
    assert config["console"]["oauth"]["token-url"] == "http://localhost:1885/oauth/token"
    # The generated configuration is public; secrets reach only the stack's environment.
    for key in ("TTN_LW_HTTP_COOKIE_BLOCK_KEY", "TTN_LW_HTTP_COOKIE_HASH_KEY"):
        assert services["stack"]["environment"][key] not in config_file.read_text(encoding="utf-8")
