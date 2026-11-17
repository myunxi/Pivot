"""Pivot runtime configuration: env overrides, token lookup, data dir privacy."""

import stat
from pathlib import Path

import pytest

from pivot.config import ConfigError, PivotConfig


def test_defaults(tmp_path: Path) -> None:
    cfg = PivotConfig.from_env({}, home=tmp_path)

    assert cfg.data_dir == tmp_path / ".pivot"
    assert cfg.rules_path == tmp_path / ".pivot" / "rules.json"
    assert cfg.store_path == tmp_path / ".pivot" / "store.db"
    assert cfg.daemon_url == "http://127.0.0.1:8765"
    assert cfg.token_path == tmp_path / ".vicoa" / "direct" / "token"
    assert cfg.serve_host == "127.0.0.1"
    assert cfg.serve_port == 8787
    assert cfg.concurrency_limit == 4


def test_env_overrides(tmp_path: Path) -> None:
    cfg = PivotConfig.from_env(
        {
            "PIVOT_DATA_DIR": str(tmp_path / "state"),
            "PIVOT_RULES_PATH": str(tmp_path / "custom" / "rules.json"),
            "PIVOT_STORE": str(tmp_path / "custom" / "pivot.db"),
            "PIVOT_DAEMON_URL": "http://127.0.0.1:9999/",
            "PIVOT_TOKEN_PATH": str(tmp_path / "token"),
            "PIVOT_SERVE_HOST": "127.0.0.2",
            "PIVOT_SERVE_PORT": "9000",
            "PIVOT_CONCURRENCY_LIMIT": "7",
        },
        home=tmp_path,
    )

    assert cfg.data_dir == tmp_path / "state"
    assert cfg.rules_path == tmp_path / "custom" / "rules.json"
    assert cfg.store_path == tmp_path / "custom" / "pivot.db"
    assert cfg.daemon_url == "http://127.0.0.1:9999"
    assert cfg.token_path == tmp_path / "token"
    assert cfg.serve_host == "127.0.0.2"
    assert cfg.serve_port == 9000
    assert cfg.concurrency_limit == 7


def test_data_dir_drives_default_paths(tmp_path: Path) -> None:
    cfg = PivotConfig.from_env(
        {"PIVOT_DATA_DIR": str(tmp_path / "state")}, home=tmp_path
    )

    assert cfg.rules_path == tmp_path / "state" / "rules.json"
    assert cfg.store_path == tmp_path / "state" / "store.db"


def test_env_paths_expand_user(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = PivotConfig.from_env({"PIVOT_STORE": "~/elsewhere/store.db"}, home=tmp_path)

    assert cfg.store_path == tmp_path / "elsewhere" / "store.db"


@pytest.mark.parametrize(
    ("env", "needle"),
    [
        ({"PIVOT_SERVE_PORT": "nope"}, "PIVOT_SERVE_PORT"),
        ({"PIVOT_SERVE_PORT": "0"}, "PIVOT_SERVE_PORT"),
        ({"PIVOT_SERVE_PORT": "70000"}, "PIVOT_SERVE_PORT"),
        ({"PIVOT_CONCURRENCY_LIMIT": "zero"}, "PIVOT_CONCURRENCY_LIMIT"),
        ({"PIVOT_CONCURRENCY_LIMIT": "0"}, "PIVOT_CONCURRENCY_LIMIT"),
        ({"PIVOT_DAEMON_URL": "127.0.0.1:8765"}, "PIVOT_DAEMON_URL"),
        ({"PIVOT_DATA_DIR": ""}, "PIVOT_DATA_DIR"),
    ],
)
def test_invalid_env(env: dict[str, str], needle: str, tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=needle):
        PivotConfig.from_env(env, home=tmp_path)


def test_read_token_strips_whitespace(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("  secret-token\n", encoding="utf-8")
    cfg = PivotConfig.from_env({"PIVOT_TOKEN_PATH": str(token_file)}, home=tmp_path)

    assert cfg.read_token() == "secret-token"


def test_read_token_missing_file(tmp_path: Path) -> None:
    cfg = PivotConfig.from_env(
        {"PIVOT_TOKEN_PATH": str(tmp_path / "missing")}, home=tmp_path
    )

    with pytest.raises(ConfigError, match="token file not found"):
        cfg.read_token()


def test_read_token_rejects_empty_file(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("\n", encoding="utf-8")
    cfg = PivotConfig.from_env({"PIVOT_TOKEN_PATH": str(token_file)}, home=tmp_path)

    with pytest.raises(ConfigError, match="token file is empty"):
        cfg.read_token()


def test_ensure_data_dir_is_private(tmp_path: Path) -> None:
    cfg = PivotConfig.from_env(
        {"PIVOT_DATA_DIR": str(tmp_path / "nested" / "state")}, home=tmp_path
    )

    cfg.ensure_data_dir()

    assert cfg.data_dir.is_dir()
    assert stat.S_IMODE(cfg.data_dir.stat().st_mode) == 0o700
