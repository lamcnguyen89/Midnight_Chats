"""Midnight Chats: a small, local Flask interface for Ollama."""

import json
import logging
import signal
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter, sleep
from uuid import uuid4

import requests
from flask import Flask, Response, g, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from web.config import load_config, server_config
from web.hardware import memory_usage
from web.logging_config import RequestLoggingMiddleware, setup_logging
from web.network import AliasPublisher

logger = logging.getLogger("midnight.app")


def create_app(test_config=None):
    config = load_config()
    app = Flask(
        __name__,
        template_folder="web/templates",
        static_folder="web/static",
        instance_relative_config=True,
    )
    app.config.from_mapping(
        DATABASE=config.get("MIDNIGHT_DATABASE", str(Path(app.instance_path) / "chats.sqlite3")),
        OLLAMA_URL=config.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/"),
        OLLAMA_TIMEOUT=float(config.get("OLLAMA_TIMEOUT", "600")),
        OLLAMA_UNLOAD_TIMEOUT=max(0, float(config.get("OLLAMA_UNLOAD_TIMEOUT", "30"))),
        MAX_CONTENT_LENGTH=4 * 1024 * 1024,
        LOG_DIR=config.get("LOG_DIR", "logs"),
        LOG_LEVEL=config.get("LOG_LEVEL", "INFO"),
        LOG_MAX_BYTES=config.get("LOG_MAX_BYTES", 10 * 1024 * 1024),
        LOG_BACKUP_COUNT=config.get("LOG_BACKUP_COUNT", 5),
    )
    if test_config:
        app.config.update(test_config)
    setup_logging(app.config)
    app.wsgi_app = RequestLoggingMiddleware(app.wsgi_app)
    logger.info("application_starting")
    Path(app.config["DATABASE"]).parent.mkdir(parents=True, exist_ok=True)

    def db():
        if "db" not in g:
            logger.debug("database_connection_opening")
            g.db = sqlite3.connect(app.config["DATABASE"], timeout=10)
            g.db.row_factory = sqlite3.Row
        return g.db

    @app.teardown_appcontext
    def close_db(_error):
        connection = g.pop("db", None)
        if connection is not None:
            connection.close()
            logger.debug("database_connection_closed")

    with app.app_context():
        db().execute("""CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, model TEXT NOT NULL,
            messages TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )""")
        db().execute("""CREATE TABLE IF NOT EXISTS model_preferences (
            model TEXT PRIMARY KEY, hidden INTEGER NOT NULL CHECK (hidden IN (0, 1))
        )""")
        db().commit()
    logger.info("database_ready")

    def payload():
        data = request.get_json()
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object.")
        return data

    def validate_model(model):
        if not isinstance(model, str) or not model.strip() or len(model) > 200:
            raise ValueError("Choose an installed model.")
        return model.strip()

    def validate_chat(data):
        model = validate_model(data.get("model"))
        messages = data.get("messages")
        if not isinstance(messages, list) or not messages or len(messages) > 1000:
            raise ValueError("A conversation needs between 1 and 1,000 messages.")
        cleaned = []
        for index, message in enumerate(messages):
            if not isinstance(message, dict):
                raise ValueError("Invalid message.")
            role, content = message.get("role"), message.get("content")
            if role != ("user" if index % 2 == 0 else "assistant"):
                raise ValueError("Messages must alternate between user and assistant.")
            if not isinstance(content, str) or not content.strip() or len(content) > 100000:
                raise ValueError("Each message must contain between 1 and 100,000 characters.")
            cleaned.append({"role": role, "content": content,
                            "interrupted": message.get("interrupted") is True})
        return model.strip(), cleaned

    def upstream_error(response):
        try:
            detail = response.json().get("error", "")
        except (ValueError, AttributeError):
            detail = ""
        return str(detail)[:500] or f"Ollama returned HTTP {response.status_code}."

    @app.errorhandler(ValueError)
    def invalid(error):
        logger.warning("request_validation_failed", extra={"fields": {"reason": str(error)}})
        return jsonify(error=str(error)), 400

    @app.errorhandler(HTTPException)
    def http_error(error):
        logger.warning("http_error", extra={"fields": {"status": error.code}})
        if request.path.startswith("/api/"):
            return jsonify(error=error.description), error.code
        return error

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/system/vram")
    def vram():
        return jsonify(memory_usage()), {"Cache-Control": "no-store"}

    @app.route("/api/model-preferences", methods=["GET", "PATCH"])
    def model_preferences():
        if request.method == "PATCH":
            visibility = payload().get("visibility")
            if not isinstance(visibility, dict) or not visibility:
                raise ValueError("Provide model visibility settings.")
            # Validate the entire update before writing anything. Use exact names,
            # including tags, so hiding one version does not hide another.
            for model, visible in visibility.items():
                if validate_model(model) != model or not isinstance(visible, bool):
                    raise ValueError("Each model visibility setting must be true or false.")
            with db():
                for model, visible in visibility.items():
                    if visible:
                        db().execute("DELETE FROM model_preferences WHERE model = ?", (model,))
                    else:
                        db().execute("INSERT OR REPLACE INTO model_preferences (model, hidden) VALUES (?, 1)", (model,))
        hidden = [row["model"] for row in db().execute(
            "SELECT model FROM model_preferences WHERE hidden = 1 ORDER BY model")]
        return jsonify(hidden_models=hidden), {"Cache-Control": "no-store"}

    @app.get("/api/models")
    def models():
        started = perf_counter()
        logger.debug("ollama_models_requested")
        try:
            with requests.get(f"{app.config['OLLAMA_URL']}/api/tags", timeout=(5, 15)) as response:
                if not response.ok:
                    logger.warning("ollama_models_http_error", extra={"fields": {"status": response.status_code}})
                    return jsonify(error=upstream_error(response)), 502
                data = response.json()
                names = sorted({item["name"] for item in data["models"]
                                if isinstance(item.get("name"), str)})
                logger.info("ollama_models_loaded", extra={"fields": {
                    "model_count": len(names), "duration_ms": round((perf_counter() - started) * 1000, 2)}})
                return jsonify(models=names)
        except requests.RequestException as error:
            logger.error("ollama_models_connection_failed", extra={"fields": {"error_type": type(error).__name__}})
            return jsonify(error="Cannot reach Ollama. Check that it is running and OLLAMA_URL is correct."), 503
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            logger.error("ollama_models_invalid_response", extra={"fields": {"error_type": type(error).__name__}})
            return jsonify(error="Ollama returned an invalid model list."), 502

    @app.post("/api/models/unload")
    def unload_model():
        return unload_one_model(validate_model(payload().get("model")))

    def model_key(name):
        # Ollama may report an implicit tag as :latest in /api/ps.
        return name if ":" in name.rsplit("/", 1)[-1] else name + ":latest"

    def running_models():
        status = requests.get(f"{app.config['OLLAMA_URL']}/api/ps", timeout=(5, 15))
        try:
            if not status.ok:
                raise requests.HTTPError(response=status)
            entries = status.json().get("models")
            if not isinstance(entries, list):
                raise ValueError("Invalid running-model list")
            names = []
            for entry in entries:
                name = entry.get("name") or entry.get("model")
                if not isinstance(name, str) or not name:
                    raise ValueError("Invalid running model")
                names.append(validate_model(name))
            return list(dict.fromkeys(names))
        finally:
            status.close()

    @app.post("/api/models/unload-all")
    def unload_all_models():
        payload()
        started = perf_counter()
        logger.info("ollama_clear_memory_started")
        try:
            models = running_models()
            failures = []
            for model in models:
                result = app.make_response(unload_one_model(model))
                if result.status_code != 200:
                    failures.append(model)
            remaining = running_models() if models else []
            if remaining:
                logger.warning("ollama_clear_memory_incomplete", extra={"fields": {
                    "remaining_models": remaining, "failed_models": failures}})
                return jsonify(error="Some Ollama models are still loaded. Wait for active requests to finish, then try clearing memory again.",
                               cleared=False, remaining_models=remaining), 502
            logger.info("ollama_clear_memory_finished", extra={"fields": {
                "model_count": len(models), "duration_ms": round((perf_counter() - started) * 1000, 2)}})
            return jsonify(cleared=True, unloaded_models=models, remaining_models=[])
        except requests.Timeout:
            logger.warning("ollama_clear_memory_timeout")
            return jsonify(error="Ollama took too long to report loaded models. Please try clearing memory again."), 504
        except requests.RequestException as error:
            logger.error("ollama_clear_memory_connection_failed", extra={"fields": {"error_type": type(error).__name__}})
            return jsonify(error="Could not read Ollama's loaded models. Check that Ollama is running, then try again."), 503
        except (ValueError, TypeError, AttributeError) as error:
            logger.error("ollama_clear_memory_invalid_response", extra={"fields": {"error_type": type(error).__name__}})
            return jsonify(error="Ollama returned an invalid list of loaded models. Please try clearing memory again."), 502

    def unload_one_model(model):
        started = perf_counter()
        details = {"model": model}
        logger.info("ollama_model_unload_started", extra={"fields": details})

        def still_loaded():
            return model_key(model) in {model_key(name) for name in running_models()}

        try:
            response = requests.post(
                f"{app.config['OLLAMA_URL']}/api/generate",
                json={"model": model, "keep_alive": 0, "stream": False},
                timeout=(5, app.config["OLLAMA_TIMEOUT"]),
            )
            try:
                # A removed model can no longer be resolved by /api/generate.
                # Verify it is absent from memory before allowing the switch.
                if not response.ok and response.status_code != 404:
                    logger.warning("ollama_model_unload_http_error", extra={"fields": {
                        **details, "status": response.status_code}})
                    return jsonify(error=upstream_error(response)), 502
                if response.ok and response.json().get("done") is not True:
                    raise ValueError("Invalid unload acknowledgement")
            finally:
                response.close()

            deadline = perf_counter() + app.config["OLLAMA_UNLOAD_TIMEOUT"]
            while still_loaded():
                if perf_counter() >= deadline:
                    logger.warning("ollama_model_unload_not_released", extra={"fields": details})
                    return jsonify(error="The previous model is still loaded. Wait for active requests to finish, then try switching again."), 504
                sleep(0.1)
            logger.info("ollama_model_unloaded", extra={"fields": {
                **details, "duration_ms": round((perf_counter() - started) * 1000, 2)}})
            return jsonify(model=model, unloaded=True)
        except requests.Timeout as error:
            logger.warning("ollama_model_unload_timeout", extra={"fields": {**details, "error_type": type(error).__name__}})
            return jsonify(error="Ollama took too long to unload the previous model. Please try switching again."), 504
        except requests.HTTPError as error:
            logger.error("ollama_model_unload_verification_failed", extra={"fields": {
                **details, "status": error.response.status_code}})
            return jsonify(error="Could not verify that Ollama released the previous model. Please try switching again."), 502
        except requests.RequestException as error:
            logger.error("ollama_model_unload_connection_failed", extra={"fields": {
                **details, "error_type": type(error).__name__}})
            return jsonify(error="Cannot reach Ollama to unload the previous model. Check that it is running, then try switching again."), 503
        except (ValueError, TypeError, AttributeError) as error:
            logger.error("ollama_model_unload_invalid_response", extra={"fields": {
                **details, "error_type": type(error).__name__}})
            return jsonify(error="Ollama returned an invalid response while unloading the previous model. Please try switching again."), 502

    @app.post("/api/chat")
    def chat():
        model, messages = validate_chat(payload())
        if messages[-1]["role"] != "user":
            raise ValueError("The last message must be from the user.")
        started = perf_counter()
        details = {"request_id": request.environ.get("midnight.request_id"), "model": model,
                   "message_count": len(messages), "input_characters": sum(len(item["content"]) for item in messages)}
        logger.info("ollama_chat_started", extra={"fields": details})
        try:
            upstream = requests.post(
                f"{app.config['OLLAMA_URL']}/api/chat",
                json={"model": model, "messages": [
                    {"role": item["role"], "content": item["content"]} for item in messages
                ], "stream": True},
                stream=True, timeout=(5, app.config["OLLAMA_TIMEOUT"]),
            )
        except requests.RequestException as error:
            logger.error("ollama_chat_connection_failed", extra={"fields": {
                **details, "error_type": type(error).__name__, "duration_ms": round((perf_counter() - started) * 1000, 2)}})
            return jsonify(error="Cannot reach Ollama. Check that it is running, then try again."), 503
        if not upstream.ok:
            logger.warning("ollama_chat_http_error", extra={"fields": {**details, "status": upstream.status_code}})
            try:
                return jsonify(error=upstream_error(upstream)), 502
            finally:
                upstream.close()

        def event(data):
            return json.dumps(data, ensure_ascii=False) + "\n"

        def generate():
            outcome = "disconnected"
            output_characters = 0
            chunk_count = 0
            first_token_ms = None
            try:
                for line in upstream.iter_lines(chunk_size=1):
                    if not line:
                        continue
                    chunk = json.loads(line)
                    chunk_count += 1
                    if chunk.get("error"):
                        outcome = "upstream_error"
                        logger.error("ollama_stream_error", extra={"fields": details})
                        yield event({"error": str(chunk["error"])[:500]})
                        return
                    message = chunk.get("message", {})
                    content = message.get("content", "")
                    if not isinstance(content, str):
                        raise ValueError("Invalid content")
                    if content:
                        output_characters += len(content)
                        if first_token_ms is None:
                            first_token_ms = round((perf_counter() - started) * 1000, 2)
                        yield event({"content": content})
                    elif message.get("thinking"):
                        yield event({"thinking": True})
                    if chunk.get("done"):
                        outcome = "completed"
                        logger.debug("ollama_generation_metrics", extra={"fields": {
                            **details, **{key: chunk[key] for key in (
                                "total_duration", "load_duration", "prompt_eval_count", "prompt_eval_duration",
                                "eval_count", "eval_duration") if isinstance(chunk.get(key), (int, float))}}})
                        yield event({"done": True})
                        return
                outcome = "incomplete"
                logger.warning("ollama_stream_incomplete", extra={"fields": details})
                yield event({"error": "Ollama closed the response before it finished. Please try again."})
            except (requests.RequestException, ValueError, TypeError, AttributeError) as error:
                outcome = "failed"
                logger.error("ollama_stream_failed", extra={"fields": {**details, "error_type": type(error).__name__}})
                yield event({"error": "The response was interrupted. Check Ollama and try again."})
            finally:
                upstream.close()
                logger.log(logging.INFO if outcome == "completed" else logging.WARNING,
                           "ollama_chat_finished", extra={"fields": {
                               **details, "outcome": outcome, "output_characters": output_characters,
                               "chunk_count": chunk_count, "first_token_ms": first_token_ms,
                               "duration_ms": round((perf_counter() - started) * 1000, 2)}})

        result = Response(generate(), mimetype="application/x-ndjson", headers={
            "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
        })
        result.call_on_close(upstream.close)
        return result

    @app.get("/api/conversations")
    def conversations():
        rows = db().execute("""SELECT id, name, model, created_at, updated_at
                               FROM conversations ORDER BY updated_at DESC, id""").fetchall()
        logger.debug("conversations_listed", extra={"fields": {"count": len(rows)}})
        return jsonify(conversations=[dict(row) for row in rows])

    @app.get("/api/conversations/<conversation_id>")
    def conversation(conversation_id):
        row = db().execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        if row is None:
            logger.info("conversation_not_found", extra={"fields": {"conversation_id": conversation_id}})
            return jsonify(error="Conversation not found."), 404
        result = dict(row)
        result["messages"] = json.loads(result["messages"])
        logger.debug("conversation_loaded", extra={"fields": {"conversation_id": conversation_id,
                                                               "message_count": len(result["messages"])}})
        return jsonify(result)

    def save(conversation_id=None):
        data = payload()
        name = data.get("name")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
            raise ValueError("Give your conversation a name (1–120 characters).")
        model, messages = validate_chat(data)
        now = datetime.now(timezone.utc).isoformat()
        if conversation_id:
            action = "conversation_updated"
            cursor = db().execute("""UPDATE conversations SET name = ?, model = ?, messages = ?,
                                     updated_at = ? WHERE id = ?""",
                                  (name.strip(), model, json.dumps(messages), now, conversation_id))
            if cursor.rowcount == 0:
                logger.info("conversation_not_found", extra={"fields": {"conversation_id": conversation_id}})
                return jsonify(error="Conversation not found."), 404
        else:
            action = "conversation_created"
            conversation_id = str(uuid4())
            db().execute("INSERT INTO conversations VALUES (?, ?, ?, ?, ?, ?)",
                         (conversation_id, name.strip(), model, json.dumps(messages), now, now))
        db().commit()
        logger.info(action, extra={"fields": {"conversation_id": conversation_id, "model": model,
                                              "message_count": len(messages)}})
        return conversation(conversation_id)

    app.add_url_rule("/api/conversations", "create_conversation", save, methods=["POST"])
    app.add_url_rule("/api/conversations/<conversation_id>", "update_conversation", save, methods=["PUT"])

    @app.delete("/api/conversations/<conversation_id>")
    def delete_conversation(conversation_id):
        cursor = db().execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        db().commit()
        if cursor.rowcount == 0:
            logger.info("conversation_not_found", extra={"fields": {"conversation_id": conversation_id}})
            return jsonify(error="Conversation not found."), 404
        logger.info("conversation_deleted", extra={"fields": {"conversation_id": conversation_id}})
        return "", 204

    logger.info("application_ready")
    return app


def main():
    setup_logging()
    settings = server_config()
    app = create_app()
    publisher = AliasPublisher(settings)

    def terminate(_signal, _frame):
        raise SystemExit(0)

    previous_handler = signal.signal(signal.SIGTERM, terminate)
    try:
        logger.info("server_starting", extra={"fields": {"host": settings.host, "port": settings.port,
                                                         "alias": settings.alias, "server": "flask"}})
        publisher.start()
        app.run(host=settings.host, port=settings.port, debug=False, load_dotenv=False)
    finally:
        publisher.stop()
        logger.info("server_stopped", extra={"fields": {"server": "flask"}})
        signal.signal(signal.SIGTERM, previous_handler)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logger.exception("server_startup_failed")
        raise
