"""Structured, rotating logs shared safely by server processes and threads."""

import json
import logging
import logging.config
import os
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from flask import has_request_context, request

from web.config import PROJECT_ROOT, load_config

_configured = None


class JsonFormatter(logging.Formatter):
    def format(self, record):
        fields = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "pid": record.process,
            "thread": record.threadName,
        }
        if has_request_context():
            fields["request_id"] = request.environ.get("midnight.request_id")
        fields.update(getattr(record, "fields", {}))
        if record.exc_info:
            fields["traceback"] = self.formatException(record.exc_info)
        return json.dumps(fields, ensure_ascii=False, default=str)


def logging_config(values=None):
    values = load_config() if values is None else values
    directory = Path(values.get("LOG_DIR", "logs")).expanduser()
    if not directory.is_absolute():
        directory = PROJECT_ROOT / directory
    directory.mkdir(parents=True, exist_ok=True)
    level = str(values.get("LOG_LEVEL", "INFO")).upper()
    if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ValueError("LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL.")
    try:
        max_bytes = int(values.get("LOG_MAX_BYTES", 10 * 1024 * 1024))
        backups = int(values.get("LOG_BACKUP_COUNT", 5))
    except (TypeError, ValueError):
        raise ValueError("LOG_MAX_BYTES and LOG_BACKUP_COUNT must be positive integers.") from None
    if max_bytes < 1 or backups < 1:
        raise ValueError("LOG_MAX_BYTES and LOG_BACKUP_COUNT must be positive integers.")

    def handler(filename):
        return {"class": "concurrent_log_handler.ConcurrentRotatingFileHandler",
                "filename": str(directory / filename), "maxBytes": max_bytes,
                "backupCount": backups, "encoding": "utf-8", "formatter": "json"}

    def logger(handlers, logger_level=level):
        return {"handlers": handlers, "level": logger_level, "propagate": False}

    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"json": {"()": "web.logging_config.JsonFormatter"}},
        "handlers": {"application": handler("application.log"),
                     "access": handler("access.log"), "server": handler("server.log"),
                     "discard": {"class": "logging.NullHandler"}},
        "loggers": {
            "midnight.app": logger(["application"]),
            "midnight.access": logger(["access"], "INFO"),
            "main": logger(["application"]),
            "__main__": logger(["application"]),
            "web.network": logger(["application"]),
            "gunicorn.error": logger(["server"]),
            # The WSGI middleware writes one access record per request, including streams.
            "gunicorn.access": logger(["discard"]),
            "werkzeug": logger(["server"], "WARNING"),
        },
        "root": {"level": "WARNING", "handlers": ["server"]},
    }


def setup_logging(values=None, force=False):
    global _configured
    configuration = logging_config(values)
    signature = (os.getpid(), configuration)
    if force or _configured != signature:
        logging.config.dictConfig(configuration)
        _configured = signature


class LoggedResponse:
    """Keep request timing and cleanup accurate until a streaming response closes."""

    def __init__(self, iterable, finish, failed):
        self.iterable = iterable
        self.iterator = iter(iterable)
        self.finish = finish
        self.failed = failed
        self.bytes_sent = 0
        self.finished = False

    def __iter__(self):
        return self

    def end(self, outcome):
        if not self.finished:
            self.finished = True
            self.finish(outcome, self.bytes_sent)

    def __next__(self):
        try:
            chunk = next(self.iterator)
            self.bytes_sent += len(chunk)
            return chunk
        except StopIteration:
            self.end("completed")
            raise
        except Exception:
            self.failed()
            self.end("failed")
            raise

    def close(self):
        try:
            close = getattr(self.iterable, "close", None)
            if close is not None:
                close()
        finally:
            self.end("disconnected")


class RequestLoggingMiddleware:
    def __init__(self, application):
        self.application = application

    def __call__(self, environ, start_response):
        started = perf_counter()
        request_id = uuid4().hex
        environ["midnight.request_id"] = request_id
        fields = {"request_id": request_id, "method": environ.get("REQUEST_METHOD"),
                  "path": environ.get("PATH_INFO"), "client_ip": environ.get("REMOTE_ADDR")}
        status_code = 500
        logger = logging.getLogger("midnight.app")
        logger.debug("request_started", extra={"fields": fields})

        def capture(status, headers, exc_info=None):
            nonlocal status_code
            status_code = int(status.split(" ", 1)[0])
            headers = [(key, value) for key, value in headers if key.lower() != "x-request-id"]
            headers.append(("X-Request-ID", request_id))
            return start_response(status, headers, exc_info)

        def failed():
            logger.exception("request_failed", extra={"fields": fields})

        def finish(outcome, bytes_sent):
            level = logging.ERROR if status_code >= 500 or outcome == "failed" else (
                logging.WARNING if status_code >= 400 or outcome == "disconnected" else logging.INFO)
            logging.getLogger("midnight.access").log(level, "request_finished", extra={"fields": {
                **fields, "status": status_code, "outcome": outcome, "bytes_sent": bytes_sent,
                "duration_ms": round((perf_counter() - started) * 1000, 2),
            }})

        try:
            iterable = self.application(environ, capture)
        except Exception:
            failed()
            finish("failed", 0)
            raise
        return LoggedResponse(iterable, finish, failed)
