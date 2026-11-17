"""The ``python -m pivot`` entry points: cron output and fail-fast errors."""

import json
from pathlib import Path

import pytest

from pivot.cli import main


def cron_payload() -> dict:
    return {
        "version": 1,
        "agents": {"claude": {"type": "claude"}},
        "rules": [
            {
                "name": "nightly",
                "when": {"cron": "0 9 * * 1-5", "timezone": "UTC"},
                "to": "claude",
                "cwd": "/tmp/project",
                "worktree": "new",
            }
        ],
    }


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    token = tmp_path / "token"
    token.write_text("token\n", encoding="utf-8")
    monkeypatch.setenv("PIVOT_DATA_DIR", str(data))
    monkeypatch.setenv("PIVOT_RULES_PATH", str(data / "rules.json"))
    monkeypatch.setenv("PIVOT_STORE", str(data / "store.db"))
    monkeypatch.setenv("PIVOT_TOKEN_PATH", str(token))
    monkeypatch.setenv("PIVOT_DAEMON_URL", "http://127.0.0.1:8765")
    return data


def test_cron_prints_baseline(cli_env: Path, capsys) -> None:
    (cli_env / "rules.json").write_text(json.dumps(cron_payload()), encoding="utf-8")

    assert main(["cron"]) == 0

    lines = [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if line.strip()
    ]
    assert [line["status"] for line in lines] == ["baseline"]
    assert lines[0]["rule"] == "nightly"


def test_cron_without_rules_file_is_a_clean_noop(cli_env: Path, capsys) -> None:
    assert main(["cron"]) == 0

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "no rules file" in captured.err


def test_missing_token_fails_fast(
    cli_env: Path, tmp_path: Path, monkeypatch, capsys
) -> None:
    (cli_env / "rules.json").write_text(json.dumps(cron_payload()), encoding="utf-8")
    monkeypatch.setenv("PIVOT_TOKEN_PATH", str(tmp_path / "missing-token"))

    assert main(["cron"]) == 1
    assert "token file not found" in capsys.readouterr().err


def test_invalid_rules_fails_fast(cli_env: Path, capsys) -> None:
    (cli_env / "rules.json").write_text('{"version": 1,', encoding="utf-8")

    assert main(["cron"]) == 1
    assert "invalid JSON" in capsys.readouterr().err
