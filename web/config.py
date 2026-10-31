"""Read project-local configuration without changing the process environment."""

import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_config(env_file=None):
    values = dotenv_values(env_file if env_file is not None else PROJECT_ROOT / ".env")
    return {**{key: value for key, value in values.items() if value is not None}, **os.environ}


@dataclass(frozen=True)
class ServerConfig:
    host: str
    port: int
    alias: str
    address: str

    @property
    def bind(self):
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{host}:{self.port}"


def server_config(env_file=None):
    values = load_config(env_file)
    host = values.get("MIDNIGHT_HOST", "127.0.0.1").strip()
    if not host or "://" in host or "/" in host:
        raise ValueError("MIDNIGHT_HOST must be a bind address, such as 0.0.0.0, without a URL or port.")
    try:
        port = int(values.get("PORT", "5000"))
    except ValueError:
        raise ValueError("PORT must be a number between 1 and 65535.") from None
    if not 1 <= port <= 65535:
        raise ValueError("PORT must be a number between 1 and 65535.")
    alias = values.get("MIDNIGHT_ALIAS", "").strip().lower().rstrip(".")
    if alias:
        if "." not in alias:
            alias += ".local"
        label = alias.removesuffix(".local")
        if not alias.endswith(".local") or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label):
            raise ValueError("MIDNIGHT_ALIAS must be a local hostname such as midnight.local (no URL or port).")
        if host in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Set MIDNIGHT_HOST=0.0.0.0 or your LAN IP to use MIDNIGHT_ALIAS from other computers.")
    return ServerConfig(host, port, alias, values.get("MIDNIGHT_ADDRESS", "").strip())
