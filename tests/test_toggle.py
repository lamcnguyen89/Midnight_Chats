import json
import os
import subprocess
import sys

from test_install import InstallerFixture


class ToggleTests(InstallerFixture):
    def setUp(self):
        super().setUp()
        self.state_file = self.root / "systemd-state.json"
        self.environment["TOGGLE_TEST_STATE"] = str(self.state_file)
        self.set_state("inactive", "not-found")
        self.write_command("systemctl", r'''
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with open(os.environ["INSTALL_TEST_COMMANDS"], "a") as output:
    output.write(json.dumps(["systemctl", *args]) + "\n")
path = Path(os.environ["TOGGLE_TEST_STATE"])
state = json.loads(path.read_text())
if args[0] == "show":
    print(state["active"] if "--property=ActiveState" in args else state["loaded"])
elif args[0] == "stop":
    state["active"] = "inactive"
elif args[0] == "disable":
    state["enabled"] = False
elif args[0] == "enable":
    state["enabled"] = True
elif args[0] == "daemon-reload":
    state["loaded"] = "loaded" if Path(os.environ["INSTALL_TEST_UNIT"]).exists() else "not-found"
elif args[0] == "unmask":
    state["loaded"] = "not-found"
elif args[0] in {"start", "restart"}:
    state["active"] = "active"
elif args[0] == "is-active":
    raise SystemExit(0 if state["active"] == "active" else 3)
path.write_text(json.dumps(state))
''')
        self.write_command("rm", r'''
import json, os, sys
args = sys.argv[1:]
with open(os.environ["INSTALL_TEST_COMMANDS"], "a") as output:
    output.write(json.dumps(["rm", *args]) + "\n")
if args[-1] == "/etc/systemd/system/midnight-chats.service":
    args[-1] = os.environ["INSTALL_TEST_UNIT"]
os.execv("/usr/bin/rm", ["/usr/bin/rm", *args])
''')

    def set_state(self, active, loaded):
        self.state_file.write_text(json.dumps({"active": active, "loaded": loaded, "enabled": loaded == "loaded"}))

    def run_toggle(self, *arguments):
        return subprocess.run([str(self.project / "scripts/toggle_services.sh"), *arguments],
                              cwd=self.root, env=self.environment, text=True, capture_output=True, timeout=20)

    def test_absent_service_installs_enables_and_starts(self):
        result = self.run_toggle()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.unit.exists())
        state = json.loads(self.state_file.read_text())
        self.assertEqual(state["active"], "active")
        self.assertTrue(state["enabled"])
        self.assertIn(["systemctl", "start", "midnight-chats.service"], self.calls())
        self.assertTrue((self.project / ".env").exists())

    def test_running_service_is_removed_and_data_is_preserved(self):
        self.set_state("active", "loaded")
        self.unit.write_text("installed unit\n")
        (self.project / ".env").write_text("PORT=7777\n")
        (self.project / "instance").mkdir()
        (self.project / "instance/chats.sqlite3").write_bytes(b"saved chats")
        (self.project / "logs").mkdir()
        (self.project / "logs/application.log").write_text("saved logs\n")
        result = self.run_toggle()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.unit.exists())
        self.assertEqual((self.project / ".env").read_text(), "PORT=7777\n")
        self.assertEqual((self.project / "instance/chats.sqlite3").read_bytes(), b"saved chats")
        self.assertEqual((self.project / "logs/application.log").read_text(), "saved logs\n")
        self.assertTrue((self.project / "systemd/midnight-chats.service").exists())
        self.assertFalse((self.project / ".venv").exists())
        self.assertIn(["systemctl", "stop", "midnight-chats.service"], self.calls())
        self.assertIn(["systemctl", "disable", "midnight-chats.service"], self.calls())
        self.assertFalse(any("ollama.service" in call or "avahi-daemon.service" in call for call in self.calls()))

    def test_stopped_failed_and_masked_units_are_started(self):
        for active, loaded in (("inactive", "loaded"), ("failed", "loaded"), ("deactivating", "loaded"), ("inactive", "masked")):
            with self.subTest(active=active, loaded=loaded):
                self.set_state(active, loaded)
                self.unit.write_text("old unit\n")
                result = self.run_toggle()
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                state = json.loads(self.state_file.read_text())
                self.assertEqual(state["active"], "active")
                self.assertTrue(state["enabled"])
        self.assertIn(["systemctl", "unmask", "midnight-chats.service"], self.calls())

    def test_starting_or_reloading_service_is_toggled_off(self):
        for active in ("activating", "reloading"):
            with self.subTest(active=active):
                self.set_state(active, "loaded")
                self.unit.write_text("installed unit\n")
                result = self.run_toggle()
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertFalse(self.unit.exists())
                self.assertEqual(json.loads(self.state_file.read_text())["active"], "inactive")

    def test_second_toggle_removes_previously_started_service(self):
        first = self.run_toggle()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        second = self.run_toggle()
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertFalse(self.unit.exists())
        self.assertEqual(json.loads(self.state_file.read_text())["active"], "inactive")

    def test_verified_manual_instance_is_stopped_before_systemd_start(self):
        (self.project / ".venv/bin").mkdir(parents=True)
        gunicorn = self.project / ".venv/bin/gunicorn"
        gunicorn.write_text("import time\ntime.sleep(60)\n")
        process = subprocess.Popen([sys.executable, str(gunicorn), "main:create_app()"], cwd=self.project)
        try:
            (self.project / "instance").mkdir()
            (self.project / "instance/gunicorn.pid").write_text(str(process.pid) + "\n")
            # Let the isolated installer create its Python wrapper normally.
            python = self.project / ".venv/bin/python"
            python.write_bytes((self.bin / "python3").read_bytes())
            python.chmod(0o755)
            result = self.run_toggle()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIsNotNone(process.poll())
            self.assertFalse((self.project / "instance/gunicorn.pid").exists())
            self.assertIn(["systemctl", "start", "midnight-chats.service"], self.calls())
        finally:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)

    def test_unrelated_pid_is_not_signalled(self):
        (self.project / "instance").mkdir()
        (self.project / "instance/gunicorn.pid").write_text(str(os.getpid()) + "\n")
        result = self.run_toggle()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("left untouched", result.stderr)
        self.assertNotIn(["systemctl", "start", "midnight-chats.service"], self.calls())

    def test_stale_manual_pid_file_is_cleared(self):
        (self.project / "instance").mkdir()
        pidfile = self.project / "instance/gunicorn.pid"
        pidfile.write_text(str(2 ** 30) + "\n")
        result = self.run_toggle()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(pidfile.exists())
        self.assertIn(["systemctl", "start", "midnight-chats.service"], self.calls())

    def test_failed_preparation_does_not_start_the_service(self):
        self.environment["INSTALL_TEST_FAIL_PIP"] = "1"
        result = self.run_toggle()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.unit.exists())
        self.assertNotIn(["systemctl", "start", "midnight-chats.service"], self.calls())

    def test_help_has_no_service_side_effects(self):
        result = self.run_toggle("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("Usage:", result.stdout)
        self.assertFalse(self.commands.exists())
