from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

from tools.prepare_building import ROOT, prepare, stack_config, validate_host


def test_preparation_preserves_existing_credentials_and_developer_env(tmp_path):
    developer_env = tmp_path / ".env"
    developer_env.write_text("LOCAL_TEST_MODE=true\n", encoding="utf-8")
    env_file, stack_file = prepare("192.168.10.20", "admin@building.local", tmp_path)
    env_bytes = env_file.read_bytes()
    config_bytes = stack_file.read_bytes()
    with pytest.raises(FileExistsError):
        prepare("192.168.10.21", "admin@building.local", tmp_path)
    assert env_file.read_bytes() == env_bytes
    assert stack_file.read_bytes() == config_bytes
    assert developer_env.read_text(encoding="utf-8") == "LOCAL_TEST_MODE=true\n"
    values = dict(line.split("=", 1) for line in env_bytes.decode().splitlines()
                  if line and not line.startswith("#"))
    assert "replace-with" not in "\n".join(values.values())
    assert "SBMS_IMAGE" not in values
    assert len(bytes.fromhex(values["LORAWAN_APP_KEY"])) == 16
    assert len(bytes.fromhex(values["TTS_COOKIE_BLOCK_KEY"])) == 32
    assert len(bytes.fromhex(values["TTS_COOKIE_HASH_KEY"])) == 64
    assert len(bytes.fromhex(values["TTS_CLUSTER_KEY"])) == 16
    assert len(set(values[key] for key in ("TTS_POSTGRES_PASSWORD", "TTS_CONSOLE_SECRET", "TTN_WEBHOOK_SECRET"))) == 3
    if os.name != "nt":
        assert env_file.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "0.0.0.0", "224.1.1.1", "::1",
                                  "192.168.1.999", "http://building.lan", "building.lan:1885",
                                  "bad\nhost", "$(whoami)", "bad_host"])
def test_invalid_server_addresses_are_rejected(host):
    with pytest.raises(ValueError):
        validate_host(host)


@pytest.mark.parametrize("path", ["http", "grpc", "interop", "gs.mqtt", "gs.mqtt-v2",
                                  "gs.basic-station", "gs.ttigw", "as.mqtt"])
def test_lan_stack_disables_tls_listeners_without_certificates(path):
    options = stack_config("pulse.building.lan")
    for section in path.split("."):
        options = options[section]
    assert options["listen-tls"] == ""


def test_building_compose_builds_local_app_alongside_stack_dependencies(tmp_path):
    if not shutil.which("docker"):
        pytest.skip("Docker CLI is required to validate Compose")
    env_file, config_file = prepare("pulse.building.lan", "admin@building.local", tmp_path)
    shutil.copy2(ROOT / "docker-compose.building.yml", tmp_path)
    shutil.copy2(ROOT / "Dockerfile", tmp_path)
    shutil.copy2(ROOT / ".docker-local-ca.cer.example", tmp_path)
    result = subprocess.run(
        ["docker", "compose", "--env-file", str(env_file), "-f",
         str(tmp_path / "docker-compose.building.yml"), "config", "--format", "json"],
        capture_output=True, text=True, check=True,
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
    assert app["environment"]["TTN_BASE_URL"] == "http://stack:1885"
    assert app["environment"]["THINGSBOARD_ENABLED"] == "false"
    assert all(mount["type"] == "volume" for mount in app["volumes"])
    assert not services["postgres"].get("ports")
    assert not services["redis"].get("ports")
    config = json.loads(config_file.read_text(encoding="utf-8"))
    assert config["console"]["ui"]["canonical-url"] == "http://pulse.building.lan:1885/console"
    assert config["console"]["oauth"]["token-url"] == "http://localhost:1885/oauth/token"
    # The generated configuration is public; secrets reach only the stack's environment.
    for key in ("TTN_LW_CONSOLE_OAUTH_CLIENT_SECRET", "TTN_LW_HTTP_COOKIE_BLOCK_KEY"):
        assert services["stack"]["environment"][key] not in config_file.read_text(encoding="utf-8")
