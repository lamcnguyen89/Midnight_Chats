import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from main import create_app


class AppTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.config = {"TESTING": True, "DATABASE": str(Path(self.directory.name) / "chats.sqlite3"),
                       "LOG_DIR": str(Path(self.directory.name) / "logs")}
        self.app = create_app(self.config)
        self.client = self.app.test_client()
        self.chat = {"name": "My first chat", "model": "llama3.2:3b", "messages": [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!"},
        ]}

    def tearDown(self):
        self.directory.cleanup()

    def test_page_and_local_assets(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Midnight Chats", response.data)
        for asset in ("/static/app.js", "/static/style.css"):
            with self.client.get(asset) as response:
                self.assertEqual(response.status_code, 200)

    def test_named_conversation_survives_restart_and_can_be_renamed_and_deleted(self):
        response = self.client.post("/api/conversations", json=self.chat)
        self.assertEqual(response.status_code, 200)
        saved = response.get_json()
        self.assertTrue(saved["created_at"].endswith("+00:00"))
        client = create_app(self.config).test_client()
        self.assertEqual(client.get(f"/api/conversations/{saved['id']}").json["messages"][1]["content"], "Hi!")
        self.assertEqual(len(client.get("/api/conversations").json["conversations"]), 1)
        updated = client.put(f"/api/conversations/{saved['id']}", json={**self.chat, "name": "New name"}).json
        self.assertEqual(updated["name"], "New name")
        self.assertEqual(updated["created_at"], saved["created_at"])
        self.assertGreaterEqual(updated["updated_at"], saved["updated_at"])
        self.assertEqual(client.delete(f"/api/conversations/{saved['id']}").status_code, 204)
        self.assertEqual(client.get(f"/api/conversations/{saved['id']}").status_code, 404)
        self.assertEqual(client.get("/api/conversations").json["conversations"], [])

    def test_saving_requires_manual_nonempty_name(self):
        for name in (None, "", "   ", 123, "a" * 121):
            with self.subTest(name=name):
                response = self.client.post("/api/conversations", json={**self.chat, "name": name})
                self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get("/api/conversations").json["conversations"], [])

    def test_invalid_history_is_rejected(self):
        for messages in ([], [None], [{"role": "system", "content": "hello"}],
                         [{"role": "user", "content": ""}], self.chat["messages"] * 2 + [{"role": "user", "content": 5}]):
            with self.subTest(messages=messages):
                self.assertEqual(self.client.post("/api/chat", json={"model": "test", "messages": messages}).status_code, 400)
        self.assertEqual(self.client.post("/api/chat", json=[]).status_code, 400)
        self.assertEqual(self.client.post("/api/chat", data="bad json", content_type="application/json").status_code, 400)
        self.assertEqual(self.client.post("/api/chat", json=self.chat).status_code, 400)

    def test_unknown_ids_do_not_create_records(self):
        self.assertEqual(self.client.put("/api/conversations/missing", json=self.chat).status_code, 404)
        self.assertEqual(self.client.delete("/api/conversations/missing").status_code, 404)

    def unload_response(self, post, get, status=200, running=None):
        response = Mock(ok=status == 200, status_code=status)
        response.json.return_value = {"done": True, "done_reason": "unload"} if status == 200 else {"error": "model unavailable"}
        post.return_value = response
        status_response = Mock(ok=True, status_code=200)
        status_response.json.return_value = {"models": running or []}
        get.return_value = status_response
        return response, status_response

    @patch("main.requests.get")
    @patch("main.requests.post")
    def test_unload_uses_zero_keep_alive_and_verifies_memory_release(self, post, get):
        response, status = self.unload_response(post, get)
        result = self.client.post("/api/models/unload", json={"model": "llama3.2:3b"})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json, {"model": "llama3.2:3b", "unloaded": True})
        self.assertTrue(post.call_args.args[0].endswith("/api/generate"))
        self.assertEqual(post.call_args.kwargs["json"], {"model": "llama3.2:3b", "keep_alive": 0, "stream": False})
        self.assertTrue(get.call_args.args[0].endswith("/api/ps"))
        post.assert_called_once()
        response.close.assert_called_once()
        status.close.assert_called_once()

    @patch("main.sleep")
    @patch("main.requests.get")
    @patch("main.requests.post")
    def test_unload_waits_for_running_model_to_disappear(self, post, get, sleep):
        _, status = self.unload_response(post, get)
        status.json.side_effect = [{"models": [{"name": "llama3.2:3b"}]}, {"models": []}]
        result = self.client.post("/api/models/unload", json={"model": "llama3.2:3b"})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(get.call_count, 2)
        sleep.assert_called_once_with(0.1)

    @patch("main.requests.get")
    @patch("main.requests.post")
    def test_unload_does_not_claim_success_while_model_remains_loaded(self, post, get):
        self.app.config["OLLAMA_UNLOAD_TIMEOUT"] = 0
        self.unload_response(post, get, running=[{"model": "example:latest"}])
        result = self.client.post("/api/models/unload", json={"model": "example"})
        self.assertEqual(result.status_code, 504)
        self.assertNotIn("unloaded", result.json)

    @patch("main.requests.get")
    @patch("main.requests.post")
    def test_missing_model_is_safe_to_switch_away_from_only_if_not_loaded(self, post, get):
        self.unload_response(post, get, status=404)
        self.assertEqual(self.client.post("/api/models/unload", json={"model": "removed"}).status_code, 200)
        self.unload_response(post, get, status=404, running=[{"name": "removed:latest"}])
        self.app.config["OLLAMA_UNLOAD_TIMEOUT"] = 0
        self.assertEqual(self.client.post("/api/models/unload", json={"model": "removed"}).status_code, 504)

    @patch("main.requests.post")
    def test_unload_rejects_invalid_model_without_calling_ollama(self, post):
        for model in (None, "", "   ", 123, "x" * 201):
            with self.subTest(model=model):
                self.assertEqual(self.client.post("/api/models/unload", json={"model": model}).status_code, 400)
        post.assert_not_called()

    @patch("main.requests.get")
    @patch("main.requests.post")
    def test_clear_all_unloads_every_running_model_and_verifies_empty(self, post, get):
        _, status = self.unload_response(post, get)
        status.json.side_effect = [
            {"models": [{"name": "alpha"}, {"name": "beta"}, {"name": "alpha"}]},
            {"models": [{"name": "beta"}]}, {"models": []}, {"models": []},
        ]
        response = self.client.post("/api/models/unload-all", json={})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["cleared"])
        self.assertEqual(response.json["unloaded_models"], ["alpha", "beta"])
        self.assertEqual(response.json["remaining_models"], [])
        self.assertEqual([call.kwargs["json"] for call in post.call_args_list], [
            {"model": "alpha", "keep_alive": 0, "stream": False},
            {"model": "beta", "keep_alive": 0, "stream": False},
        ])

    @patch("main.requests.get")
    @patch("main.requests.post")
    def test_clear_all_when_nothing_is_loaded_does_not_load_anything(self, post, get):
        self.unload_response(post, get)
        response = self.client.post("/api/models/unload-all", json={})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["unloaded_models"], [])
        post.assert_not_called()
        get.assert_called_once()

    @patch("main.requests.get")
    @patch("main.requests.post")
    def test_clear_all_continues_after_partial_failure_and_reports_remaining_models(self, post, get):
        success, status = self.unload_response(post, get)
        failure = Mock(ok=False, status_code=500)
        failure.json.return_value = {"error": "cannot unload alpha"}
        post.side_effect = [failure, success]
        status.json.side_effect = [
            {"models": [{"name": "alpha"}, {"name": "beta"}]},
            {"models": [{"name": "alpha"}]}, {"models": [{"name": "alpha"}]},
        ]
        response = self.client.post("/api/models/unload-all", json={})
        self.assertEqual(response.status_code, 502)
        self.assertFalse(response.json["cleared"])
        self.assertEqual(response.json["remaining_models"], ["alpha"])
        self.assertEqual(post.call_count, 2)

    @patch("main.requests.get")
    @patch("main.requests.post")
    def test_clear_all_detects_models_reloaded_by_another_client(self, post, get):
        _, status = self.unload_response(post, get)
        status.json.side_effect = [
            {"models": [{"name": "alpha"}]}, {"models": [{"name": "new-model"}]},
            {"models": [{"name": "new-model"}]},
        ]
        response = self.client.post("/api/models/unload-all", json={})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json["remaining_models"], ["new-model"])

    @patch("main.requests.get")
    @patch("main.requests.post")
    def test_clear_all_does_not_claim_success_if_model_stays_loaded(self, post, get):
        self.app.config["OLLAMA_UNLOAD_TIMEOUT"] = 0
        self.unload_response(post, get, running=[{"name": "alpha"}])
        response = self.client.post("/api/models/unload-all", json={})
        self.assertEqual(response.status_code, 502)
        self.assertFalse(response.json["cleared"])

    @patch("main.requests.get")
    @patch("main.requests.post")
    def test_clear_all_handles_offline_timeout_and_invalid_status(self, post, get):
        get.side_effect = requests.ConnectionError()
        self.assertEqual(self.client.post("/api/models/unload-all", json={}).status_code, 503)
        get.side_effect = requests.Timeout()
        self.assertEqual(self.client.post("/api/models/unload-all", json={}).status_code, 504)
        get.side_effect = None
        _, status = self.unload_response(post, get)
        status.json.return_value = {"invalid": []}
        self.assertEqual(self.client.post("/api/models/unload-all", json={}).status_code, 502)
        post.assert_not_called()

    @patch("main.requests.get")
    def test_clear_all_requires_a_json_object(self, get):
        self.assertEqual(self.client.post("/api/models/unload-all", data="").status_code, 415)
        self.assertEqual(self.client.post("/api/models/unload-all", json=[]).status_code, 400)
        get.assert_not_called()

    @patch("main.requests.get")
    @patch("main.requests.post")
    def test_unload_handles_upstream_http_errors_and_invalid_acknowledgements(self, post, get):
        response, _ = self.unload_response(post, get, status=500)
        self.assertEqual(self.client.post("/api/models/unload", json={"model": "example"}).status_code, 502)
        response.close.assert_called_once()
        get.assert_not_called()
        response, _ = self.unload_response(post, get)
        response.json.return_value = {"done": False}
        self.assertEqual(self.client.post("/api/models/unload", json={"model": "example"}).status_code, 502)

    @patch("main.requests.get")
    @patch("main.requests.post")
    def test_unload_handles_network_failure_and_timeout(self, post, get):
        post.side_effect = requests.ConnectionError()
        self.assertEqual(self.client.post("/api/models/unload", json={"model": "example"}).status_code, 503)
        post.side_effect = requests.Timeout()
        self.assertEqual(self.client.post("/api/models/unload", json={"model": "example"}).status_code, 504)
        get.assert_not_called()

    @patch("main.requests.get")
    @patch("main.requests.post")
    def test_unload_cannot_succeed_if_running_model_status_is_invalid(self, post, get):
        _, status = self.unload_response(post, get)
        status.json.return_value = {"unexpected": []}
        self.assertEqual(self.client.post("/api/models/unload", json={"model": "example"}).status_code, 502)
        status.ok = False
        status.status_code = 503
        self.assertEqual(self.client.post("/api/models/unload", json={"model": "example"}).status_code, 502)

    @patch("main.requests.get")
    def test_models_and_offline_error(self, get):
        response = get.return_value.__enter__.return_value
        response.ok = True
        response.json.return_value = {"models": [{"name": "b"}, {"name": "a"}, {"name": "a"}]}
        self.assertEqual(self.client.get("/api/models").json, {"models": ["a", "b"]})
        response.json.return_value = {"models": []}
        self.assertEqual(self.client.get("/api/models").json, {"models": []})
        response.json.return_value = {"unexpected": []}
        self.assertEqual(self.client.get("/api/models").status_code, 502)
        get.side_effect = requests.ConnectionError()
        self.assertEqual(self.client.get("/api/models").status_code, 503)

    def mock_stream(self, post, chunks):
        upstream = Mock()
        upstream.ok = True
        upstream.iter_lines.return_value = iter(json.dumps(chunk).encode() for chunk in chunks)
        post.return_value = upstream
        return upstream

    def request_stream(self):
        return self.client.post("/api/chat", json={"model": "test", "messages": self.chat["messages"][:1]})

    @patch("main.requests.post")
    def test_streaming_preserves_unicode_and_does_not_save_automatically(self, post):
        upstream = self.mock_stream(post, [{"message": {"thinking": "hmm"}},
            {"message": {"content": "Hello ☾"}}, {"message": {"content": "!"}, "done": True}])
        with self.request_stream() as response:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, "application/x-ndjson")
            chunks = [json.loads(line) for line in response.data.splitlines()]
        self.assertEqual(chunks, [{"thinking": True}, {"content": "Hello ☾"}, {"content": "!"}, {"done": True}])
        upstream.close.assert_called()
        self.assertTrue(post.call_args.kwargs["stream"])
        self.assertEqual(self.client.get("/api/conversations").json["conversations"], [])

    @patch("main.requests.post")
    def test_incomplete_and_invalid_streams_report_errors(self, post):
        for chunks in ([{"message": {"content": "partial"}}], [{"error": "model failed"}]):
            with self.subTest(chunks=chunks):
                upstream = self.mock_stream(post, chunks)
                with self.request_stream() as response:
                    self.assertIn("error", json.loads(response.data.splitlines()[-1]))
                upstream.close.assert_called()
        upstream = self.mock_stream(post, [])
        upstream.iter_lines.return_value = iter([b"invalid json"])
        with self.request_stream() as response:
            self.assertIn("error", json.loads(response.data))

    @patch("main.requests.post")
    def test_upstream_connection_and_http_errors(self, post):
        post.side_effect = requests.ConnectionError()
        self.assertEqual(self.request_stream().status_code, 503)
        post.side_effect = None
        upstream = post.return_value
        upstream.ok = False
        upstream.json.return_value = {"error": "model not found"}
        self.assertEqual(self.request_stream().json["error"], "model not found")
        upstream.close.assert_called_once()

    @patch("main.requests.post")
    def test_interrupted_reply_is_saved_but_metadata_not_sent_to_ollama(self, post):
        self.chat["messages"][1]["interrupted"] = True
        saved = self.client.post("/api/conversations", json=self.chat).json
        self.assertTrue(saved["messages"][1]["interrupted"])
        self.mock_stream(post, [{"done": True}])
        with self.client.post("/api/chat", json={**self.chat, "messages": self.chat["messages"] + [
            {"role": "user", "content": "Continue"}]}) as response:
            self.assertEqual(response.status_code, 200)
            _ = response.data
        self.assertNotIn("interrupted", post.call_args.kwargs["json"]["messages"][1])


if __name__ == "__main__":
    unittest.main()
