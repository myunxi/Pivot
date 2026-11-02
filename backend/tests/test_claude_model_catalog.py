"""`build_claude_available_models` curated `~/.claude/models.json` behaviour."""

import json
from pathlib import Path

from integrations.headless.claude_model_catalog import build_claude_available_models


def _write_models_json(home: Path, data) -> None:
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    (home / ".claude" / "models.json").write_text(json.dumps(data), encoding="utf-8")


def test_curated_models_json_replaces_catalog(tmp_path: Path) -> None:
    _write_models_json(
        tmp_path,
        [{"id": "deepseek-v4.1-flash", "label": "deepseek-flash-4.1"}],
    )
    assert build_claude_available_models(home=tmp_path, env={}) == [
        {"id": "deepseek-v4.1-flash", "label": "deepseek-flash-4.1", "is_default": True},
    ]


def test_curated_dedupes_and_skips_empty(tmp_path: Path) -> None:
    _write_models_json(
        tmp_path,
        [
            "deepseek-v4.1-flash",
            {"id": "deepseek-v4.1-flash", "label": "dup"},
            {"id": "", "label": "empty-id"},
            {"id": "deepseek-v4-pro"},
            "   ",
        ],
    )
    assert build_claude_available_models(home=tmp_path, env={}) == [
        {"id": "deepseek-v4.1-flash", "label": "deepseek-v4.1-flash", "is_default": True},
        {"id": "deepseek-v4-pro", "label": "deepseek-v4-pro"},
    ]


def test_missing_or_malformed_models_json_falls_back(tmp_path: Path) -> None:
    # Missing file → catalog fallback (non-empty).
    assert len(build_claude_available_models(home=tmp_path, env={})) > 0

    # Malformed JSON → fallback, not a crash.
    (tmp_path / ".claude").mkdir(exist_ok=True)
    (tmp_path / ".claude" / "models.json").write_text("{ not json", encoding="utf-8")
    assert len(build_claude_available_models(home=tmp_path, env={})) > 0


def test_all_invalid_entries_falls_back(tmp_path: Path) -> None:
    _write_models_json(tmp_path, [])
    assert len(build_claude_available_models(home=tmp_path, env={})) > 0

    _write_models_json(tmp_path, [{"label": "no-id"}, 42, None])
    assert len(build_claude_available_models(home=tmp_path, env={})) > 0


def test_default_flag_marks_is_default_else_first_entry(tmp_path: Path) -> None:
    _write_models_json(
        tmp_path,
        [
            {"id": "deepseek-v4.1-flash", "label": "flash"},
            {"id": "deepseek-v4-pro", "label": "pro", "default": True},
        ],
    )
    assert build_claude_available_models(home=tmp_path, env={}) == [
        {"id": "deepseek-v4.1-flash", "label": "flash"},
        {"id": "deepseek-v4-pro", "label": "pro", "is_default": True},
    ]
