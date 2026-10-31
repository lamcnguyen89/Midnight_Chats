import os
import runpy
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from main import create_app
from web.config import PROJECT_ROOT, ServerConfig, load_config, server_config
from web.logging_config import setup_logging
from web.network import AliasPublisher, lan_address


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / ".env"
        environment = patch.dict("os.environ", {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def test_env_file_drives_alias_bind_and_port(self):
        self.path.write_text("MIDNIGHT_HOST=0.0.0.0\nMIDNIGHT_ALIAS=ThorChat\nPORT=5050\n")
        settings = server_config(self.path)
        self.assertEqual(settings.alias, "thorchat.local")
        self.assertEqual(settings.bind, "0.0.0.0:5050")

    def test_exported_settings_override_file_and_missing_file_uses_defaults(self):
        self.path.write_text("MIDNIGHT_HOST=0.0.0.0\nPORT=5050\n")
        with patch.dict("os.environ", {"PORT": "6060"}):
            self.assertEqual(server_config(self.path).port, 6060)
        self.assertEqual(server_config(self.path.with_name("missing")).bind, "127.0.0.1:5000")
        self.assertEqual(load_config(self.path)["PORT"], "5050")

    def test_invalid_alias_or_port_is_rejected(self):
        for value in ("0", "65536", "invalid", ""):
            self.path.write_text(f"PORT={value}\n")
            with self.subTest(port=value), self.assertRaisesRegex(ValueError, "PORT"):
                server_config(self.path)
        for alias in ("https://chat.local", "chat.local:5050", "example.com", "chat room.local", "-chat.local"):
            self.path.write_text(f"MIDNIGHT_HOST=0.0.0.0\nMIDNIGHT_ALIAS={alias}\n")
            with self.subTest(alias=alias), self.assertRaisesRegex(ValueError, "MIDNIGHT_ALIAS"):
                server_config(self.path)

    def test_alias_requires_lan_binding(self):
        self.path.write_text("MIDNIGHT_ALIAS=midnight.local\n")
        with self.assertRaisesRegex(ValueError, "MIDNIGHT_HOST"):
            server_config(self.path)

    def test_ipv6_binding_is_bracketed(self):
        self.path.write_text("MIDNIGHT_HOST=::\nPORT=5050\n")
        self.assertEqual(server_config(self.path).bind, "[::]:5050")

    def test_app_reads_root_env_without_changing_process_environment(self):
        self.path.write_text("OLLAMA_URL=http://127.0.0.1:12000/\nOLLAMA_TIMEOUT=42\n")
        with patch("web.config.PROJECT_ROOT", self.path.parent):
            app = create_app({"TESTING": True, "DATABASE": str(self.path.parent / "test.sqlite3"),
                              "LOG_DIR": str(self.path.parent / "logs")})
        self.assertEqual(app.config["OLLAMA_URL"], "http://127.0.0.1:12000")
        self.assertEqual(app.config["OLLAMA_TIMEOUT"], 42)
        self.assertNotIn("OLLAMA_URL", os.environ)


class AliasTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        environment = patch.dict(os.environ, {"LOG_DIR": self.directory.name})
        environment.start()
        self.addCleanup(environment.stop)
        setup_logging()

    @patch("web.network.AliasPublisher")
    @patch("web.config.server_config", return_value=ServerConfig("0.0.0.0", 5050, "chat.local", ""))
    def test_gunicorn_reload_replaces_and_cleans_up_publisher(self, config, publisher):
        first, second = Mock(), Mock()
        publisher.side_effect = [first, second]
        hooks = runpy.run_path(str(PROJECT_ROOT / "web/gunicorn.conf.py"))
        server = SimpleNamespace()
        hooks["on_starting"](server)
        hooks["on_reload"](server)
        first.stop.assert_called_once()
        self.assertIs(server.midnight_alias_publisher, second)
        hooks["on_exit"](server)
        second.stop.assert_called_once()

    def test_explicit_and_bound_lan_addresses(self):
        self.assertEqual(lan_address("0.0.0.0", "192.168.1.81"), "192.168.1.81")
        self.assertEqual(lan_address("192.168.1.81"), "192.168.1.81")
        for invalid in ("127.0.0.1", "0.0.0.0", "224.0.0.1", "not-an-ip"):
            with self.subTest(address=invalid), self.assertRaisesRegex(ValueError, "MIDNIGHT_ADDRESS"):
                lan_address("0.0.0.0", invalid)

    @patch("web.network.socket.socket")
    def test_automatic_address_uses_default_route(self, socket):
        socket.return_value.__enter__.return_value.getsockname.return_value = ("192.168.1.81", 12345)
        self.assertEqual(lan_address("0.0.0.0"), "192.168.1.81")

    @patch("web.network.subprocess.Popen")
    @patch("web.network.shutil.which", return_value="/usr/bin/avahi-publish-address")
    def test_publisher_lifecycle_and_disabled_alias(self, which, popen):
        disabled = AliasPublisher(ServerConfig("0.0.0.0", 5050, "", ""))
        disabled.start()
        popen.assert_not_called()
        publisher = AliasPublisher(ServerConfig("0.0.0.0", 5050, "chat.local", "192.168.1.81"))
        publisher.start()
        publisher.start()
        popen.assert_called_once_with(
            ["/usr/bin/avahi-publish-address", "--no-reverse", "chat.local", "192.168.1.81"], stdout=subprocess.DEVNULL)
        child = popen.return_value
        child.poll.return_value = None
        publisher.stop()
        child.terminate.assert_called_once()
        child.wait.assert_called_once_with(timeout=5)
        publisher.stop()
        self.assertIsNone(publisher.process)

    @patch("web.network.shutil.which", return_value=None)
    def test_missing_avahi_keeps_ip_access_available(self, which):
        publisher = AliasPublisher(ServerConfig("0.0.0.0", 5050, "chat.local", ""))
        with self.assertLogs("web.network", level="WARNING") as logs:
            publisher.start()
        self.assertIn("avahi-utils", logs.output[0])
        self.assertIsNone(publisher.process)

    def test_unresponsive_publisher_is_reaped(self):
        publisher = AliasPublisher(ServerConfig("0.0.0.0", 5050, "chat.local", ""))
        child = Mock()
        child.poll.return_value = None
        child.wait.side_effect = [subprocess.TimeoutExpired("avahi-publish-address", 5), 0]
        publisher.process = child
        publisher.stop()
        child.kill.assert_called_once()
        self.assertIsNone(publisher.process)
