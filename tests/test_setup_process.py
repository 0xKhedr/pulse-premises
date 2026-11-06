"""Cross-platform setup behavior, using only the standard library."""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import setup


class SetupTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.addCleanup(patch.stopall)
        patch.object(setup, "ROOT", self.root).start()
        patch.object(setup.shutil, "which", return_value="docker").start()
        self.docker_run = patch.object(setup.subprocess, "run").start()

    def invoke(self, *args):
        with patch("sys.argv", ["setup.py", *args]), contextlib.redirect_stdout(io.StringIO()):
            return setup.main()

    def test_fresh_setup_generates_config_and_starts_compose(self):
        self.assertEqual(self.invoke(), 0)
        values = (self.root / ".env").read_text()
        self.assertIn("TTS_HOST=localhost\n", values)
        self.assertIn("ADMIN_EMAIL=admin@localhost\n", values)
        self.assertIn("TTS_POSTGRES_PASSWORD=root\n", values)
        self.assertIn("TTS_CONSOLE_SECRET=console\n", values)
        self.assertIn("AUTO_ENROLLMENT_ENABLED=true\n", values)
        self.assertTrue((self.root / "deploy/stack/ttn-lw-stack.yml").is_file())
        self.assertEqual(self.docker_run.call_args_list[-1].args[0],
                         ["docker", "compose", "up", "-d", "--build", "--wait", "--wait-timeout", "300"])

    def test_prepare_only_supports_custom_host_and_email_without_docker(self):
        self.assertEqual(self.invoke("--prepare-only", "--host", "192.168.1.20",
                                     "--admin-email", "owner@example.com"), 0)
        values = (self.root / ".env").read_text()
        self.assertIn("TTS_HOST=192.168.1.20\n", values)
        self.assertIn("ADMIN_EMAIL=owner@example.com\n", values)
        self.docker_run.assert_not_called()

    def test_repeated_setup_overwrites_configuration(self):
        self.invoke("--prepare-only")
        paths = [self.root / ".env", self.root / "deploy/stack/ttn-lw-stack.yml"]
        contents = [path.read_bytes() for path in paths]
        self.assertEqual(self.invoke(), 0)
        self.assertNotEqual(paths[0].read_bytes(), contents[0])
        self.assertEqual(paths[1].read_bytes(), contents[1])
        values = paths[0].read_text()
        self.assertIn("TTS_POSTGRES_PASSWORD=root\n", values)
        self.assertIn("TTS_CONSOLE_SECRET=console\n", values)
        self.assertIn("AUTO_ENROLLMENT_ENABLED=true\n", values)

    def test_existing_env_is_overwritten_and_missing_stack_config_created(self):
        path = self.root / ".env"
        path.write_text("ADMIN_PASSWORD=existing\n")
        self.assertEqual(self.invoke("--prepare-only"), 0)
        self.assertIn("ADMIN_PASSWORD=admin\n", path.read_text())
        self.assertTrue((self.root / "deploy/stack/ttn-lw-stack.yml").is_file())
        self.docker_run.assert_not_called()

    def test_existing_configuration_accepts_host_and_email_changes(self):
        self.invoke("--prepare-only")
        self.assertEqual(self.invoke("--host", "192.168.1.21", "--admin-email", "owner@example.com"), 0)
        values = (self.root / ".env").read_text()
        self.assertIn("TTS_HOST=192.168.1.21\n", values)
        self.assertIn("ADMIN_EMAIL=owner@example.com\n", values)
        config = json.loads((self.root / "deploy/stack/ttn-lw-stack.yml").read_text())
        self.assertEqual(config["console"]["ui"]["canonical-url"], "http://192.168.1.21:1885/console")

    def test_existing_stack_config_is_overwritten_and_missing_env_created(self):
        path = self.root / "deploy/stack/ttn-lw-stack.yml"
        path.parent.mkdir(parents=True)
        path.write_text("old configuration")
        self.assertEqual(self.invoke("--prepare-only"), 0)
        self.assertTrue((self.root / ".env").is_file())
        config = json.loads(path.read_text())
        self.assertEqual(config["console"]["ui"]["canonical-url"], "http://localhost:1885/console")

    def test_invalid_host_leaves_existing_configuration_unchanged(self):
        self.invoke("--prepare-only")
        paths = [self.root / ".env", self.root / "deploy/stack/ttn-lw-stack.yml"]
        contents = [path.read_bytes() for path in paths]
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.invoke("--prepare-only", "--host", "http://invalid")
        self.assertEqual([path.read_bytes() for path in paths], contents)

    def test_missing_docker_does_not_create_configuration(self):
        with patch.object(setup.shutil, "which", return_value=None), \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.invoke()
        self.assertFalse((self.root / ".env").exists())

    def test_docker_failure_is_reported(self):
        self.docker_run.side_effect = subprocess.CalledProcessError(1, ["docker", "compose", "version"])
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            self.invoke()
        self.assertEqual(error.exception.code, 1)
        self.assertFalse((self.root / ".env").exists())


if __name__ == "__main__":
    unittest.main()
