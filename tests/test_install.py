"""Exercise the installer with isolated package/systemd commands, without root."""

import json
import os
import pwd
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless(shutil.which("systemd-analyze"), "systemd-analyze is required to validate generated units")
class InstallerFixture(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.project = self.root / "checkout space % $cash"
        for directory in ("scripts", "systemd", "web"):
            (self.project / directory).mkdir(parents=True, exist_ok=True)
        for filename in ("scripts/install.sh", "scripts/toggle_services.sh", "systemd/midnight-chats.service", "requirements.txt", ".env.example"):
            shutil.copy2(PROJECT_ROOT / filename, self.project / filename)
        for filename in ("main.py", "web/gunicorn.conf.py"):
            (self.project / filename).touch()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.commands = self.root / "commands.jsonl"
        self.unit = self.root / "installed.service"
        self.environment = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ.get("PATH", os.defpath),
                                INSTALL_TEST_COMMANDS=str(self.commands), INSTALL_TEST_UNIT=str(self.unit))
        self.environment.pop("SUDO_USER", None)
        self.environment.pop("MIDNIGHT_PYTHON", None)
        self.write_command("python3", r'''
import json, os, shutil, sys
from pathlib import Path
args = sys.argv[1:]
with open(os.environ["INSTALL_TEST_COMMANDS"], "a") as output:
    output.write(json.dumps(["python", *args]) + "\n")
if args[:2] == ["-m", "venv"]:
    target = Path(args[2]) / "bin/python"
    target.parent.mkdir(parents=True)
    shutil.copy2(__file__, target)
elif args[:2] == ["-m", "pip"]:
    if "install" in args and os.environ.get("INSTALL_TEST_FAIL_PIP"):
        raise SystemExit(42)
elif args[:2] == ["-m", "gunicorn"]:
    pass
else:
    os.execv(sys.executable, [sys.executable, *args])
''')
        self.write_command("install", r'''
import json, os, sys
args = sys.argv[1:]
with open(os.environ["INSTALL_TEST_COMMANDS"], "a") as output:
    output.write(json.dumps(["install", *args]) + "\n")
if args[-1] == "/etc/systemd/system/midnight-chats.service":
    args[-1] = os.environ["INSTALL_TEST_UNIT"]
os.execv("/usr/bin/install", ["/usr/bin/install", *args])
''')
        self.write_command("systemctl", r'''
import json, os, sys
args = sys.argv[1:]
with open(os.environ["INSTALL_TEST_COMMANDS"], "a") as output:
    output.write(json.dumps(["systemctl", *args]) + "\n")
if args[:1] == ["is-active"]:
    raise SystemExit(1)
''')
        self.write_command("sudo", r'''
import os, sys
args = sys.argv[1:]
if args and args[0] == "--":
    args = args[1:]
os.execvp(args[0], args)
''')

    def write_command(self, name, source):
        path = self.bin / name
        path.write_text(f"#!{sys.executable}\n" + source.lstrip())
        path.chmod(0o755)

    def run_installer(self, *arguments):
        return subprocess.run([str(self.project / "scripts/install.sh"), *arguments], cwd=self.root,
                              env=self.environment, text=True, capture_output=True, timeout=20)

    def calls(self):
        return [json.loads(line) for line in self.commands.read_text().splitlines()] if self.commands.exists() else []


class InstallerTests(InstallerFixture):
    def test_fresh_install_creates_env_and_renders_actual_user_and_checkout(self):
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.project / ".venv/bin/python").is_file())
        self.assertEqual((self.project / ".env").read_bytes(), (self.project / ".env.example").read_bytes())
        self.assertEqual((self.project / ".env").stat().st_mode & 0o777, 0o600)
        unit = self.unit.read_text()
        self.assertIn("User=" + pwd.getpwuid(os.getuid()).pw_name, unit)
        self.assertIn("WorkingDirectory=" + str(self.project).replace("%", "%%"), unit)
        self.assertIn("$$cash", unit)
        self.assertIn('"' + str(self.project).replace("%", "%%").replace("$", "$$") + '/.venv/bin/python" -m gunicorn', unit)
        self.assertEqual((self.project / "systemd/midnight-chats.service").read_bytes(),
                         (PROJECT_ROOT / "systemd/midnight-chats.service").read_bytes())
        self.assertIn(["systemctl", "daemon-reload"], self.calls())
        self.assertIn(["systemctl", "enable", "midnight-chats.service"], self.calls())
        self.assertNotIn(["systemctl", "restart", "midnight-chats.service"], self.calls())

    def test_reruns_preserve_configuration_saved_chats_and_logs(self):
        marker = self.root / "must-not-execute"
        configuration = f'PORT=7777\nNOTE=$(touch "{marker}")\n'
        (self.project / ".env").write_text(configuration)
        (self.project / "instance").mkdir()
        database = self.project / "instance/chats.sqlite3"
        with closing(sqlite3.connect(database)) as connection:
            connection.execute("CREATE TABLE saved (name TEXT)")
            connection.execute("INSERT INTO saved VALUES ('keep this conversation')")
            connection.commit()
        (self.project / "logs").mkdir()
        (self.project / "logs/application.log").write_text("keep existing log\n")
        for _ in range(2):
            result = self.run_installer()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.project / ".env").read_text(), configuration)
        self.assertFalse(marker.exists())
        with closing(sqlite3.connect(database)) as connection:
            self.assertEqual(connection.execute("SELECT name FROM saved").fetchall(), [("keep this conversation",)])
        self.assertEqual((self.project / "logs/application.log").read_text(), "keep existing log\n")
        creations = [call for call in self.calls() if call[:3] == ["python", "-m", "venv"]]
        self.assertEqual(len(creations), 1)

    def test_package_failure_prevents_service_installation(self):
        self.environment["INSTALL_TEST_FAIL_PIP"] = "1"
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.unit.exists())
        self.assertFalse(any(call[0] == "systemctl" for call in self.calls()))

    def test_start_option_starts_managed_service(self):
        result = self.run_installer("--start")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(["systemctl", "restart", "midnight-chats.service"], self.calls())

    def test_start_option_refuses_to_take_over_running_manual_instance(self):
        (self.project / "instance").mkdir()
        (self.project / "instance/gunicorn.pid").write_text(str(os.getpid()) + "\n")
        result = self.run_installer("--start")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("outside systemd", result.stderr)
        self.assertTrue(self.unit.exists())
        self.assertNotIn(["systemctl", "restart", "midnight-chats.service"], self.calls())

    def test_help_and_unknown_options_do_not_install_anything(self):
        result = self.run_installer("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("Usage:", result.stdout)
        result = self.run_installer("--unknown")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.project / ".venv").exists())
        self.assertFalse(self.unit.exists())
