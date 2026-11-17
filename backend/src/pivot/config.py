"""Pivot runtime configuration.

Defaults target this machine's direct-mode layout: the daemon on
``127.0.0.1:8765`` and the one direct nonce at ``~/.vicoa/direct/token``.
Pivot reuses that nonce (the upgrade guide's "same nonce" contract); set
``PIVOT_TOKEN_PATH`` to keep Pivot's credential separate.

Every field is env-overridable so the helper scripts can run a throwaway
instance without touching the long-lived ``~/.pivot`` state.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .errors import ConfigError

DEFAULT_SERVE_HOST = "127.0.0.1"
DEFAULT_SERVE_PORT = 8787
DEFAULT_DAEMON_URL = "http://127.0.0.1:8765"
DEFAULT_CONCURRENCY_LIMIT = 4


def _path_from_env(env: Mapping[str, str], name: str) -> Path | None:
    raw = env.get(name)
    if raw is None:
        return None
    if raw.strip() == "":
        raise ConfigError(f"{name} must not be empty")
    return Path(raw.strip()).expanduser()


def _int_from_env(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None:
        return default
    try:
        value = int(raw.strip())
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc
    return value


@dataclass(frozen=True)
class PivotConfig:
    data_dir: Path
    rules_path: Path
    store_path: Path
    token_path: Path
    daemon_url: str
    serve_host: str = DEFAULT_SERVE_HOST
    serve_port: int = DEFAULT_SERVE_PORT
    concurrency_limit: int = DEFAULT_CONCURRENCY_LIMIT

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        home: Path | None = None,
    ) -> "PivotConfig":
        """Build a config from ``PIVOT_*`` values, with direct-mode defaults."""
        source = os.environ if env is None else env
        home_dir = Path(home).expanduser() if home is not None else Path.home()

        data_dir = _path_from_env(source, "PIVOT_DATA_DIR") or home_dir / ".pivot"
        rules_path = (
            _path_from_env(source, "PIVOT_RULES_PATH") or data_dir / "rules.json"
        )
        store_path = _path_from_env(source, "PIVOT_STORE") or data_dir / "store.db"
        token_path = (
            _path_from_env(source, "PIVOT_TOKEN_PATH")
            or home_dir / ".vicoa" / "direct" / "token"
        )

        daemon_raw = source.get("PIVOT_DAEMON_URL", DEFAULT_DAEMON_URL).strip()
        if daemon_raw == "":
            raise ConfigError("PIVOT_DAEMON_URL must not be empty")
        if not daemon_raw.startswith(("http://", "https://")):
            raise ConfigError(
                "PIVOT_DAEMON_URL must start with http:// or https://, "
                f"got {daemon_raw!r}"
            )
        daemon_url = daemon_raw.rstrip("/")

        serve_host = source.get("PIVOT_SERVE_HOST", DEFAULT_SERVE_HOST).strip()
        if serve_host == "":
            raise ConfigError("PIVOT_SERVE_HOST must not be empty")
        serve_port = _int_from_env(source, "PIVOT_SERVE_PORT", DEFAULT_SERVE_PORT)
        if not 1 <= serve_port <= 65535:
            raise ConfigError(
                f"PIVOT_SERVE_PORT must be between 1 and 65535, got {serve_port}"
            )
        concurrency_limit = _int_from_env(
            source, "PIVOT_CONCURRENCY_LIMIT", DEFAULT_CONCURRENCY_LIMIT
        )
        if concurrency_limit < 1:
            raise ConfigError(
                f"PIVOT_CONCURRENCY_LIMIT must be at least 1, got {concurrency_limit}"
            )

        return cls(
            data_dir=data_dir,
            rules_path=rules_path,
            store_path=store_path,
            token_path=token_path,
            daemon_url=daemon_url,
            serve_host=serve_host,
            serve_port=serve_port,
            concurrency_limit=concurrency_limit,
        )

    def ensure_data_dir(self) -> None:
        """Create the data dir private (0700); pivot state holds work prompts."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.data_dir, 0o700)

    def read_token(self) -> str:
        """Return the direct-mode nonce without its trailing newline."""
        try:
            text = self.token_path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise ConfigError(f"token file not found: {self.token_path}") from exc
        except OSError as exc:
            raise ConfigError(
                f"cannot read token file {self.token_path}: {exc}"
            ) from exc
        token = text.strip()
        if token == "":
            raise ConfigError(f"token file is empty: {self.token_path}")
        return token
