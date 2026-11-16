import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from main import create_app


class ModelDeletionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.app = create_app({
            "TESTING": True,
            "DATABASE": str(Path(directory.name) / "chats.sqlite3"),
            "LOG_DIR": str(Path(directory.name) / "logs"),
        })
        self.client = self.app.test_client()

    def upstream(self, delete, status=200):
        response = Mock(status_code=status)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        delete.return_value = response
        return response

    @patch("main.requests.delete")
    def test_deletes_exact_model_and_preserves_chats_and_visibility(self, delete):
        upstream = self.upstream(delete)
        # Ollama returns an empty body on successful deletion.
        upstream.json.side_effect = ValueError("Empty response")
        name = "example:8b"
        chat = self.client.post("/api/conversations", json={
            "name": "Keep this chat", "model": name,
            "messages": [{"role": "user", "content": "Hello"}],
        }).json
        self.client.patch("/api/model-preferences", json={"visibility": {name: False}})
        response = self.client.delete("/api/models", json={"model": name})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"model": name, "deleted": True})
        delete.assert_called_once_with(
            f"{self.app.config['OLLAMA_URL']}/api/delete", json={"model": name},
            timeout=(5, self.app.config["OLLAMA_TIMEOUT"]))
        upstream.json.assert_not_called()
        upstream.__exit__.assert_called_once()
        self.assertEqual(self.client.get(f"/api/conversations/{chat['id']}").json, chat)
        self.assertEqual(self.client.get("/api/model-preferences").json, {"hidden_models": [name]})

    @patch("main.requests.delete")
    def test_invalid_requests_never_contact_ollama(self, delete):
        for data in ([], {}, {"model": None}, {"model": ""}, {"model": "   "},
                     {"model": 123}, {"model": "x" * 201}):
            with self.subTest(data=data):
                self.assertEqual(self.client.delete("/api/models", json=data).status_code, 400)
        self.assertEqual(self.client.delete("/api/models").status_code, 415)
        delete.assert_not_called()

    @patch("main.requests.delete")
    def test_upstream_errors_do_not_claim_deletion(self, delete):
        for status, expected in ((404, 404), (500, 502)):
            with self.subTest(status=status):
                upstream = self.upstream(delete, status)
                upstream.json.return_value = {"error": "Model could not be deleted"}
                response = self.client.delete("/api/models", json={"model": "example:8b"})
                self.assertEqual(response.status_code, expected)
                self.assertEqual(response.json, {"error": "Model could not be deleted"})
                upstream.__exit__.assert_called_once()

    @patch("main.requests.delete")
    def test_non_json_upstream_error(self, delete):
        upstream = self.upstream(delete, 500)
        upstream.json.side_effect = ValueError("Not JSON")
        response = self.client.delete("/api/models", json={"model": "example:8b"})
        self.assertEqual(response.status_code, 502)
        self.assertIn("HTTP 500", response.json["error"])

    @patch("main.requests.delete")
    def test_connection_failure_and_timeout(self, delete):
        for error, expected in ((requests.ConnectionError(), 503), (requests.Timeout(), 504)):
            with self.subTest(error=error):
                delete.side_effect = error
                response = self.client.delete("/api/models", json={"model": "example:8b"})
                self.assertEqual(response.status_code, expected)
                self.assertIn("error", response.json)
                self.assertNotIn("deleted", response.json)


if __name__ == "__main__":
    unittest.main()
