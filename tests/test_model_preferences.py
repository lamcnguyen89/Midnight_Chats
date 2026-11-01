import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

from main import create_app


class ModelPreferenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = {
            "TESTING": True,
            "DATABASE": str(Path(self.directory.name) / "chats.sqlite3"),
            "LOG_DIR": str(Path(self.directory.name) / "logs"),
        }
        self.client = create_app(self.config).test_client()

    def update(self, visibility, client=None):
        return (client or self.client).patch("/api/model-preferences", json={"visibility": visibility})

    @patch("main.requests.post")
    @patch("main.requests.get")
    def test_preferences_default_to_visible_without_contacting_ollama(self, get, post):
        response = self.client.get("/api/model-preferences")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"hidden_models": []})
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(self.update({"example:8b": False}).json, {"hidden_models": ["example:8b"]})
        get.assert_not_called()
        post.assert_not_called()

    def test_hidden_models_survive_restart_and_can_be_shown_from_another_client(self):
        self.assertEqual(self.update({"example:8b": False, "example:70b": False}).status_code, 200)
        client = create_app(self.config).test_client()
        self.assertEqual(client.get("/api/model-preferences").json,
                         {"hidden_models": ["example:70b", "example:8b"]})
        self.assertEqual(self.update({"example:8b": True}, client).json,
                         {"hidden_models": ["example:70b"]})
        self.assertEqual(self.client.get("/api/model-preferences").json,
                         {"hidden_models": ["example:70b"]})

    def test_partial_updates_preserve_other_browsers_choices(self):
        other = create_app(self.config).test_client()
        self.update({"alpha:latest": False})
        self.update({"beta:latest": False}, other)
        self.assertEqual(self.update({"alpha:latest": True}).json,
                         {"hidden_models": ["beta:latest"]})
        # Repeated writes are harmless, and showing a new model is a no-op.
        self.assertEqual(self.update({"beta:latest": False, "new:latest": True}).json,
                         {"hidden_models": ["beta:latest"]})

    @patch("main.requests.get")
    def test_model_list_stays_complete_and_preferences_survive_model_removal(self, get):
        response = Mock(ok=True)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        get.return_value = response
        self.update({"example:8b": False})
        for names in (["example:8b", "example:70b"], [], ["example:8b", "example:70b", "new:latest"]):
            response.json.return_value = {"models": [{"name": name} for name in names]}
            self.assertEqual(self.client.get("/api/models").json, {"models": sorted(names)})
            self.assertEqual(self.client.get("/api/model-preferences").json,
                             {"hidden_models": ["example:8b"]})

    def test_invalid_updates_are_rejected_atomically(self):
        self.update({"keep:latest": False})
        invalid = [None, [], {}, {"visibility": None}, {"visibility": []}, {"visibility": {}},
                   {"visibility": {"valid:latest": False, "bad:latest": "false"}},
                   {"visibility": {"valid:latest": False, "bad:latest": 0}},
                   {"visibility": {"valid:latest": False, "bad:latest": None}},
                   {"visibility": {"valid:latest": False, "": False}},
                   {"visibility": {"valid:latest": False, " spaced:latest ": False}},
                   {"visibility": {"valid:latest": False, "x" * 201: False}}]
        for data in invalid:
            with self.subTest(data=data):
                response = self.client.patch("/api/model-preferences", data=json.dumps(data),
                                             content_type="application/json")
                self.assertEqual(response.status_code, 400)
                self.assertEqual(self.client.get("/api/model-preferences").json,
                                 {"hidden_models": ["keep:latest"]})

    def test_model_names_are_bound_as_sql_values(self):
        name = "model'); DROP TABLE conversations; --:latest"
        self.assertEqual(self.update({name: False}).json, {"hidden_models": [name]})
        self.assertEqual(self.client.get("/api/conversations").status_code, 200)

    def test_hidden_models_remain_usable_in_saved_conversations(self):
        self.update({"example:8b": False})
        response = self.client.post("/api/conversations", json={
            "name": "Hidden model chat", "model": "example:8b",
            "messages": [{"role": "user", "content": "Hello"}],
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get(f"/api/conversations/{response.json['id']}").json["model"], "example:8b")

    def test_existing_databases_gain_preferences_without_changing_chats(self):
        chat = self.client.post("/api/conversations", json={
            "name": "Existing chat", "model": "example:8b",
            "messages": [{"role": "user", "content": "Keep this"}],
        }).json
        with closing(sqlite3.connect(self.config["DATABASE"])) as connection:
            connection.execute("DROP TABLE model_preferences")
            connection.commit()
        client = create_app(self.config).test_client()
        self.assertEqual(client.get(f"/api/conversations/{chat['id']}").json, chat)
        self.assertEqual(client.get("/api/model-preferences").json, {"hidden_models": []})
        self.assertEqual(self.update({"example:8b": False}, client).status_code, 200)


if __name__ == "__main__":
    unittest.main()
