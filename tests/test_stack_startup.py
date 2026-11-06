"""Exercise the container shell scripts with a fake TTN executable."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHELL = shutil.which("sh") if os.name != "nt" else str(
    Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
)


def shell_path(path):
    value = path.as_posix()
    if os.name == "nt" and path.drive:
        value = "/" + value[0].lower() + value[2:]
    return value


@unittest.skipUnless(SHELL and Path(SHELL).is_file(), "A POSIX shell is required")
class StackStartupTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.log = self.root / "calls.log"
        self.init_script = self.root / "stack-init.sh"
        self.init_script.write_bytes((ROOT / "linux/stack-init.sh").read_bytes())
        self.init_script.chmod(0o755)
        entrypoint = (ROOT / "linux/stack-entrypoint.sh").read_text().replace(
            "/setup/stack-init.sh", shlex.quote(shell_path(self.init_script))
        )
        self.entrypoint = self.root / "entrypoint.sh"
        self.entrypoint.write_text(entrypoint, encoding="utf-8", newline="\n")
        self.entrypoint.chmod(0o755)
        command = self.root / "ttn-lw-stack"
        command.write_text('''#!/bin/sh
printf '%s\\n' "$*" >> "$CALL_LOG"
case "$*" in
  *"is-db migrate"*)
    if [ "$TEST_MODE" = migrate-fail ]; then echo migration-failed >&2; exit 7; fi ;;
  *"is-db create-admin-user"*)
    if [ "$TEST_MODE" = admin-fail ]; then echo permission_denied >&2; exit 9; fi
    if [ "$TEST_MODE" = existing ]; then echo 'error:store:already_exists' >&2; exit 6; fi
    echo test-console-secret ;;
  *"is-db create-oauth-client"*)
    if [ "$TEST_MODE" = oauth-fail ]; then echo permission_denied >&2; exit 8; fi
    if [ "$TEST_MODE" = existing ]; then echo 'error:store:already_exists' >&2; exit 6; fi
    echo test-console-secret ;;
esac
''', encoding="utf-8", newline="\n")
        command.chmod(0o755)

    def invoke(self, *args, mode="fresh"):
        env = dict(os.environ, CALL_LOG=shell_path(self.log), TEST_MODE=mode,
                   TTN_LW_CONSOLE_OAUTH_CLIENT_SECRET="test-console-secret",
                   TTN_LW_CONSOLE_UI_CANONICAL_URL="http://localhost:1885/console",
                   TTN_LW_BLOB_LOCAL_DIRECTORY=shell_path(self.root),
                   TTS_BOOTSTRAP_EMAIL="admin@127.0.0.1", TTS_BOOTSTRAP_PASSWORD="admin")
        command = "PATH=" + shlex.quote(shell_path(self.root)) + ':"$PATH"\nexec ' + " ".join(
            shlex.quote(value) for value in [shell_path(self.entrypoint), *args]
        )
        return subprocess.run([SHELL, "-c", command], env=env, capture_output=True, text=True)

    def calls(self):
        return self.log.read_text().splitlines()

    def test_start_initializes_then_executes_server_without_leaking_secrets(self):
        result = self.invoke("start")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertIn("is-db migrate", calls[0])
        self.assertIn("create-admin-user --id admin --email admin@127.0.0.1 --password admin", calls[1])
        self.assertIn("create-oauth-client", calls[2])
        self.assertTrue(calls[3].endswith(" start"))
        self.assertNotIn("test-console-secret", result.stdout + result.stderr)

    def test_existing_resources_are_preserved_and_server_starts(self):
        result = self.invoke("start", mode="existing")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("TTN administrator already exists; preserving it.", result.stdout)
        self.assertIn("Console OAuth client already exists; preserving it.", result.stdout)
        self.assertTrue(self.calls()[-1].endswith(" start"))

    def test_migration_failure_prevents_bootstrap_and_server_start(self):
        result = self.invoke("start", mode="migrate-fail")
        self.assertEqual(result.returncode, 7)
        self.assertEqual(len(self.calls()), 1)

    def test_admin_failure_stops_before_oauth_and_server(self):
        result = self.invoke("start", mode="admin-fail")
        self.assertEqual(result.returncode, 9)
        self.assertEqual(len(self.calls()), 2)
        self.assertIn("permission_denied", result.stderr)

    def test_oauth_failure_prevents_server_start(self):
        result = self.invoke("start", mode="oauth-fail")
        self.assertEqual(result.returncode, 8)
        self.assertEqual(len(self.calls()), 3)

    def test_cli_commands_bypass_initialization(self):
        result = self.invoke("is-db", "migrate")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), ["-c /config/ttn-lw-stack.yml is-db migrate"])


    def test_second_start_skips_completed_bootstrap_steps(self):
        self.assertEqual(self.invoke("start").returncode, 0)
        self.log.write_text("")
        result = self.invoke("start", mode="admin-fail")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.calls()), 2)
        self.assertIn("is-db migrate", self.calls()[0])
        self.assertTrue(self.calls()[1].endswith(" start"))

    def test_interrupted_setup_resumes_without_recreating_admin(self):
        self.assertEqual(self.invoke("start", mode="oauth-fail").returncode, 8)
        self.log.write_text("")
        result = self.invoke("start", mode="admin-fail")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.calls()), 3)
        self.assertIn("create-oauth-client", self.calls()[1])
        self.assertTrue(self.calls()[2].endswith(" start"))


if __name__ == "__main__":
    unittest.main()
