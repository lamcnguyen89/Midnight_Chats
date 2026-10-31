"""Gunicorn settings shared with the main.py launcher via the root .env file."""

import logging

from web.config import server_config
from web.logging_config import logging_config, setup_logging
from web.network import AliasPublisher

setup_logging()
logger = logging.getLogger("midnight.app")
try:
    settings = server_config()
except Exception:
    logger.exception("server_configuration_failed")
    raise
bind = settings.bind
workers = 1
threads = 4
timeout = 660
logconfig_dict = logging_config()
accesslog = None
errorlog = "-"


def _start_alias(server):
    publisher = AliasPublisher(settings)
    publisher.start()
    server.midnight_alias_publisher = publisher


def on_starting(server):
    logger.info("server_starting", extra={"fields": {"server": "gunicorn", "bind": bind, "alias": settings.alias}})
    _start_alias(server)


def post_fork(server, worker):
    # Each process needs independent file locks for safe concurrent rotation.
    setup_logging(force=True)


def on_reload(server):
    logger.info("server_configuration_reloaded", extra={"fields": {"bind": bind, "alias": settings.alias}})
    publisher = getattr(server, "midnight_alias_publisher", None)
    if publisher is not None:
        publisher.stop()
    _start_alias(server)


def on_exit(server):
    publisher = getattr(server, "midnight_alias_publisher", None)
    if publisher is not None:
        publisher.stop()
    logger.info("server_stopped", extra={"fields": {"server": "gunicorn"}})
