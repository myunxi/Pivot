"""Build Claude's live available-model list for the mid-session gear.

Claude Code exposes no ``model/list`` RPC the way codex's app-server does
(``codex_app_server.discover_and_report_models``), so there is nothing to probe
at runtime. When ``~/.claude/models.json`` exists it is the *entire* list; the
fallback is the static agent catalog merged with any custom models the user has
configured on this machine — the same merge paseo does in
``providers/claude/models.ts``: the ``model`` field of
``~/.claude/settings.json`` plus any ``ANTHROPIC_*_MODEL`` environment override
(``ANTHROPIC_MODEL``, ``ANTHROPIC_DEFAULT_SONNET_MODEL``,
``ANTHROPIC_SMALL_FAST_MODEL``, …). The Claude Agent SDK the runner spawns reads
those very sources, so surfacing them keeps the picker honest about what Claude
will actually accept.

Imports the canonical catalog from ``protocol.agent_catalog`` (shipped in the
CLI wheel); the ``shared.agent_catalog`` shim is not shipped to the daemon.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Mapping, NotRequired, Optional, TypedDict

from protocol.agent_catalog import AGENT_CATALOG

logger = logging.getLogger(__name__)

# ANTHROPIC_MODEL, ANTHROPIC_DEFAULT_OPUS_MODEL, ANTHROPIC_SMALL_FAST_MODEL, …
_ANTHROPIC_MODEL_ENV = re.compile(r"^ANTHROPIC_[A-Z0-9_]*MODEL$")


class ClaudeModel(TypedDict):
    """A model the renderer's picker can show: ``id`` + display ``label``.

    ``is_default`` marks the model new sessions should start in. Only the
    curated ``~/.claude/models.json`` path sets it; the catalog/derived fallback
    leaves it off and lets the static catalog keep its own default.
    """

    id: str
    label: str
    is_default: NotRequired[bool]


def _catalog_claude_models() -> list[ClaudeModel]:
    """The catalog's Claude models as ``[{id, label}]`` (order preserved)."""
    for agent in AGENT_CATALOG.get("agents", []):
        if agent.get("id") == "claude":
            return [
                {"id": m["id"], "label": str(m.get("label") or m["id"])}
                for m in agent.get("models", [])
                if m.get("id")
            ]
    return []


def _custom_claude_model_ids(home: Path, env: Mapping[str, str]) -> list[str]:
    """User-configured Claude model ids from settings.json + env, in a stable
    order (settings.json first, then env keys sorted). Best-effort: an
    unreadable / malformed settings.json contributes nothing."""
    ids: list[str] = []

    settings_path = home / ".claude" / "settings.json"
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            model = data.get("model")
            if isinstance(model, str) and model.strip():
                ids.append(model.strip())
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as exc:
        logger.info(
            "claude model discovery: could not read %s (%s)", settings_path, exc
        )

    for key in sorted(env):
        if _ANTHROPIC_MODEL_ENV.match(key):
            value = env.get(key)
            if isinstance(value, str) and value.strip():
                ids.append(value.strip())

    return ids


def _models_json_entries(home: Path) -> list[ClaudeModel] | None:
    """The curated ``~/.claude/models.json`` list, or ``None`` to fall back.

    Accepts a JSON array whose items are either a plain model-id string or
    ``{"id", "label"?, "default"?}``. De-dupes by id and drops empty ids. The
    entry with ``"default": true`` is marked ``is_default``; when no entry says
    so, the first entry is. Returns ``None`` (not ``[]``) when the file is
    missing, malformed, or yields no usable entries, so callers keep their
    existing catalog merge.
    """
    path = home / ".claude" / "models.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        logger.info("claude model discovery: could not read %s (%s)", path, exc)
        return None
    if not isinstance(raw, list):
        return None

    entries: list[ClaudeModel] = []
    default_id: str | None = None
    seen: set[str] = set()
    for item in raw:
        if isinstance(item, str):
            model_id, label, is_default = item.strip(), None, False
        elif isinstance(item, dict):
            model_id = str(item.get("id", "")).strip()
            label = item.get("label")
            label = str(label).strip() if isinstance(label, str) else None
            is_default = bool(item.get("default"))
        else:
            continue
        if not model_id or model_id in seen:
            continue
        entry: ClaudeModel = {"id": model_id, "label": label or model_id}
        if is_default and default_id is None:
            default_id = model_id
        entries.append(entry)
        seen.add(model_id)

    if not entries:
        return None
    if default_id is None:
        default_id = entries[0]["id"]
    for entry in entries:
        if entry["id"] == default_id:
            entry["is_default"] = True
    return entries


def build_claude_available_models(
    home: Optional[Path] = None,
    env: Optional[Mapping[str, str]] = None,
) -> list[ClaudeModel]:
    """The machine's Claude model list for the picker.

    When ``~/.claude/models.json`` exists and parses to a non-empty list, that
    curated list is returned as-is (including its ``is_default`` marker) — the
    picker is then fully user-controlled. Otherwise falls back to the static
    catalog merged with the machine's custom models (settings.json ``model`` +
    ``ANTHROPIC_*_MODEL`` env), de-duped by id. Returns ``[]`` only when the
    catalog somehow has no Claude entry — callers treat empty as "nothing to
    report" and leave the static fallback in place.
    """
    home = home or Path.home()
    env = env if env is not None else os.environ

    curated = _models_json_entries(home)
    if curated is not None:
        return curated

    models = _catalog_claude_models()
    seen = {m["id"] for m in models}
    for model_id in _custom_claude_model_ids(home, env):
        if model_id not in seen:
            models.append({"id": model_id, "label": model_id})
            seen.add(model_id)
    return models
