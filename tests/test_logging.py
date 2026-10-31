import json
import logging
import multiprocessing
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from main import create_app
from web.logging_config import logging_config, setup_logging


def write_parallel_logs(directory, worker):
    setup_logging({"LOG_DIR": directory, "LOG_MAX_BYTES": 1024, "LOG_BACKUP_COUNT": 100}, force=True)
    for index in range(30):
        logging.getLogger("midnight.app").info("parallel_logging_test", extra={"fields": {
            "worker": worker, "index": index}})
    logging.shutdown()


class LoggingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.logs = self.path / "logs"
        self.app = create_app({"TESTING": True, "DATABASE": str(self.path / "test.sqlite3"),
                               "LOG_DIR": str(self.logs), "LOG_LEVEL": "DEBUG"})
        self.client = self.app.test_client()

    def records(self, filename="application.log"):
        path = self.logs / filename
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_access_records_are_correlated_and_exclude_queries_and_headers(self):
        with self.client.get("/?token=private-query-value", headers={
            "X-Request-ID": "untrusted-request-id", "Authorization": "Bearer private-token"}) as response:
            _ = response.data
            request_id = response.headers["X-Request-ID"]
        records = self.records("access.log")
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["request_id"], request_id)
        self.assertNotEqual(request_id, "untrusted-request-id")
        self.assertEqual(record["path"], "/")
        self.assertEqual(record["status"], 200)
        self.assertEqual(record["outcome"], "completed")
        self.assertGreater(record["bytes_sent"], 0)
        self.assertGreaterEqual(record["duration_ms"], 0)
        self.assertTrue(record["timestamp"].endswith("+00:00"))
        self.assertIn("pid", record)
        self.assertIn("thread", record)
        text = json.dumps(records + self.records())
        self.assertNotIn("private-query-value", text)
        self.assertNotIn("private-token", text)
        started = next(item for item in self.records() if item["message"] == "request_started")
        self.assertEqual(started["request_id"], request_id)

    @patch("main.requests.post")
    def test_chat_and_save_actions_log_metadata_without_conversation_text(self, post):
        upstream = Mock(ok=True)
        upstream.iter_lines.return_value = iter([
            json.dumps({"message": {"content": "PRIVATE_REPLY"}}).encode(),
            json.dumps({"done": True, "eval_count": 5}).encode(),
        ])
        post.return_value = upstream
        messages = [{"role": "user", "content": "PRIVATE_PROMPT"}]
        with self.client.post("/api/chat", json={"model": "test-model", "messages": messages}) as response:
            _ = response.data
            request_id = response.headers["X-Request-ID"]
        saved = self.client.post("/api/conversations", json={"name": "PRIVATE_TITLE", "model": "test-model",
            "messages": messages + [{"role": "assistant", "content": "PRIVATE_REPLY"}]}).get_json()
        self.client.delete(f"/api/conversations/{saved['id']}")
        records = self.records()
        finished = next(item for item in records if item["message"] == "ollama_chat_finished")
        self.assertEqual(finished["request_id"], request_id)
        self.assertEqual(finished["model"], "test-model")
        self.assertEqual(finished["outcome"], "completed")
        self.assertEqual(finished["input_characters"], len("PRIVATE_PROMPT"))
        self.assertEqual(finished["output_characters"], len("PRIVATE_REPLY"))
        self.assertIsNotNone(finished["first_token_ms"])
        self.assertTrue(any(item["message"] == "conversation_created" for item in records))
        self.assertTrue(any(item["message"] == "conversation_deleted" for item in records))
        text = json.dumps(records + self.records("access.log") + self.records("server.log"))
        for private in ("PRIVATE_PROMPT", "PRIVATE_REPLY", "PRIVATE_TITLE"):
            self.assertNotIn(private, text)

    @patch("main.requests.post")
    def test_upstream_errors_are_logged_without_error_payload(self, post):
        upstream = Mock(ok=True)
        upstream.iter_lines.return_value = iter([b'{"error":"PRIVATE_UPSTREAM_ERROR"}'])
        post.return_value = upstream
        with self.client.post("/api/chat", json={"model": "test", "messages": [
            {"role": "user", "content": "PRIVATE_PROMPT"}]}) as response:
            _ = response.data
        records = self.records()
        self.assertTrue(any(item["message"] == "ollama_stream_error" for item in records))
        finished = next(item for item in records if item["message"] == "ollama_chat_finished")
        self.assertEqual(finished["outcome"], "upstream_error")
        self.assertNotIn("PRIVATE_UPSTREAM_ERROR", json.dumps(records))

    @patch("main.requests.post")
    def test_closing_stream_logs_disconnect_and_closes_ollama(self, post):
        upstream = Mock(ok=True)
        upstream.iter_lines.return_value = iter([b'{"message":{"content":"partial"}}'] * 5)
        post.return_value = upstream
        response = self.client.post("/api/chat", json={"model": "test", "messages": [
            {"role": "user", "content": "Hello"}]}, buffered=False)
        response.close()
        response.close()
        finished = next(item for item in self.records() if item["message"] == "ollama_chat_finished")
        self.assertEqual(finished["outcome"], "disconnected")
        access = self.records("access.log")
        self.assertEqual(len(access), 1)
        self.assertEqual(access[0]["outcome"], "disconnected")
        upstream.close.assert_called()

    def test_unexpected_errors_have_traceback_and_matching_request_id(self):
        self.app.config["PROPAGATE_EXCEPTIONS"] = False

        @self.app.get("/test-error")
        def error():
            raise RuntimeError("intentional test failure")

        with self.client.get("/test-error") as response:
            self.assertEqual(response.status_code, 500)
            _ = response.data
            request_id = response.headers["X-Request-ID"]
        error = next(item for item in self.records() if "traceback" in item)
        self.assertIn("RuntimeError: intentional test failure", error["traceback"])
        self.assertEqual(error["request_id"], request_id)
        self.assertEqual(self.records("access.log")[0]["status"], 500)

    @patch("main.requests.get", side_effect=requests.ConnectionError("PRIVATE_EXCEPTION_DETAIL"))
    def test_expected_network_error_records_type_without_exception_content(self, get):
        with self.client.get("/api/models") as response:
            self.assertEqual(response.status_code, 503)
            _ = response.data
        error = next(item for item in self.records() if item["message"] == "ollama_models_connection_failed")
        self.assertEqual(error["error_type"], "ConnectionError")
        self.assertNotIn("PRIVATE_EXCEPTION_DETAIL", json.dumps(self.records()))

    def test_rotation_is_bounded_and_preserves_valid_json(self):
        setup_logging({"LOG_DIR": str(self.logs), "LOG_MAX_BYTES": 1024, "LOG_BACKUP_COUNT": 2}, force=True)
        logger = logging.getLogger("midnight.app")
        for index in range(30):
            logger.info("rotation_test", extra={"fields": {"index": index, "padding": "x" * 100}})
        files = list(self.logs.glob("application.log*"))
        self.assertEqual(len(files), 3)
        for path in files:
            for line in path.read_text().splitlines():
                json.loads(line)
        self.assertEqual(self.records()[-1]["index"], 29)

    def test_invalid_rotation_and_level_settings_are_rejected(self):
        for values in ({"LOG_LEVEL": "invalid"}, {"LOG_MAX_BYTES": "invalid"},
                       {"LOG_MAX_BYTES": 0}, {"LOG_BACKUP_COUNT": 0}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                logging_config({"LOG_DIR": str(self.logs), **values})

    def test_processes_can_rotate_same_log_without_losing_records(self):
        directory = str(self.path / "parallel")
        context = multiprocessing.get_context("spawn")
        processes = [context.Process(target=write_parallel_logs, args=(directory, worker)) for worker in range(2)]
        try:
            for process in processes:
                process.start()
            for process in processes:
                process.join(timeout=10)
                self.assertEqual(process.exitcode, 0)
        finally:
            for process in processes:
                if process.is_alive():
                    process.kill()
                    process.join(timeout=5)
        records = [json.loads(line) for path in Path(directory).glob("application.log*")
                   for line in path.read_text().splitlines()]
        self.assertEqual({(item["worker"], item["index"]) for item in records},
                         {(worker, index) for worker in range(2) for index in range(30)})
        self.assertEqual(len(records), 60)
