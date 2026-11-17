"""Strict rules.json parsing.

The file is user-edited through the dashboard, so validation must be complete
before anything is written or reloaded: unknown fields, bad cron expressions,
unknown timezones, relative cwd values, and the worktree/read-only pairing all
fail here with a message the editor shows verbatim.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter

from .errors import RulesError
from .model import (
    DEFAULT_PROMPT,
    AgentConfig,
    EventSource,
    Rule,
    RulesFile,
    RuleWhen,
)

TOP_LEVEL_FIELDS = frozenset({"version", "agents", "rules"})
AGENT_FIELDS = frozenset({"type"})
RULE_FIELDS = frozenset(
    {
        "name",
        "when",
        "to",
        "cwd",
        "worktree",
        "read_only",
        "prompt",
        "group",
        "parallel",
        "timeout",
        "retry",
        "permission_mode",
        "model",
    }
)
WHEN_FIELDS = frozenset({"source", "types", "cron", "timezone"})
NON_CRON_SOURCES = frozenset({EventSource.MANUAL.value, EventSource.WEBHOOK.value})
WORKTREES = frozenset({"new", "none"})
PERMISSION_MODES = frozenset(
    {"default", "auto", "acceptEdits", "plan", "bypassPermissions"}
)


def load_rules(path: Path) -> RulesFile:
    """Read and validate ``path``. File errors propagate to the API layer."""
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise RulesError(f"rules file is not valid UTF-8: {path}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RulesError(
            f"invalid JSON: {exc.msg} (line {exc.lineno}, column {exc.colno})"
        ) from exc
    return parse_rules(data)


def parse_rules(data: object) -> RulesFile:
    """Validate an already-decoded rules document."""
    if not isinstance(data, dict):
        raise RulesError("rules file must be a JSON object")
    _reject_unknown(data, TOP_LEVEL_FIELDS, "rules file")

    version = data.get("version")
    if isinstance(version, bool) or version != 1:
        raise RulesError(f"version must be 1, got {version!r}")

    agents = _parse_agents(data.get("agents"))
    raw_rules = data.get("rules", [])
    if not isinstance(raw_rules, list):
        raise RulesError("rules must be a list")

    rules: list[Rule] = []
    seen: set[str] = set()
    for index, raw_rule in enumerate(raw_rules):
        rule = _parse_rule(raw_rule, agents, index)
        if rule.name in seen:
            raise RulesError(f"duplicate rule name: {rule.name}")
        seen.add(rule.name)
        rules.append(rule)

    return RulesFile(
        version=1,
        agents=MappingProxyType(agents),
        rules=tuple(rules),
    )


def _parse_agents(raw: object) -> dict[str, AgentConfig]:
    if not isinstance(raw, dict):
        raise RulesError("agents must be an object")
    agents: dict[str, AgentConfig] = {}
    for name, entry in raw.items():
        if not isinstance(name, str) or name.strip() == "":
            raise RulesError("agent names must be non-empty strings")
        agent_name = name.strip()
        if not isinstance(entry, dict):
            raise RulesError(f"agent {agent_name!r} must be an object")
        _reject_unknown(entry, AGENT_FIELDS, f"agent {agent_name!r}")
        agent_type = entry.get("type")
        if not isinstance(agent_type, str) or agent_type.strip() == "":
            raise RulesError(
                f"agent {agent_name!r}: agent type must be a non-empty string"
            )
        agents[agent_name] = AgentConfig(type=agent_type.strip())
    return agents


def _parse_rule(raw: object, agents: Mapping[str, AgentConfig], index: int) -> Rule:
    if not isinstance(raw, dict):
        raise RulesError(f"rule {index}: must be an object")

    raw_name = raw.get("name")
    if not isinstance(raw_name, str) or raw_name.strip() == "":
        raise RulesError(f"rule {index}: name must be a non-empty string")
    name = raw_name.strip()
    label = f"rule '{name}'"

    _reject_unknown(raw, RULE_FIELDS, label)
    target = _parse_target(raw.get("to"), agents, label)
    cwd = _parse_cwd(raw.get("cwd"), label)
    worktree = _parse_worktree(raw.get("worktree"), label)
    read_only = _parse_read_only(raw.get("read_only", False), label)
    if worktree == "none" and not read_only:
        raise RulesError(f"{label}: worktree 'none' requires read_only: true")

    prompt = _parse_prompt(raw.get("prompt", DEFAULT_PROMPT), label)
    group = _parse_group(raw.get("group"), label)
    parallel = _parse_parallel(raw, group, label)
    timeout = _parse_timeout(raw.get("timeout"), label)
    retry = _parse_retry(raw.get("retry", 0), label)
    permission_mode = _parse_permission_mode(raw, agents, target, label)
    model = _parse_model(raw.get("model"), label)
    if permission_mode is None and read_only and agents[target].type == "claude":
        permission_mode = "auto"

    when = _parse_when(raw.get("when"), label)

    return Rule(
        name=name,
        when=when,
        to=target,
        cwd=cwd,
        worktree=worktree,
        read_only=read_only,
        prompt=prompt,
        group=group,
        parallel=parallel,
        timeout=timeout,
        retry=retry,
        permission_mode=permission_mode,
        model=model,
    )


def _parse_target(raw: object, agents: Mapping[str, AgentConfig], label: str) -> str:
    target = raw.strip() if isinstance(raw, str) else ""
    if target == "" or target not in agents:
        raise RulesError(f"{label}: unknown agent: {target or raw!r}")
    return target


def _parse_cwd(raw: object, label: str) -> str:
    if not isinstance(raw, str) or raw.strip() == "":
        raise RulesError(f"{label}: cwd must be a non-empty string")
    cwd = raw.strip()
    if not Path(os.path.expanduser(cwd)).is_absolute():
        raise RulesError(f"{label}: cwd must be absolute or start with '~'")
    return cwd


def _parse_worktree(raw: object, label: str) -> str:
    if not isinstance(raw, str) or raw.strip() not in WORKTREES:
        raise RulesError(f"{label}: worktree must be 'new' or 'none'")
    return raw.strip()


def _parse_read_only(raw: object, label: str) -> bool:
    if not isinstance(raw, bool):
        raise RulesError(f"{label}: read_only must be a boolean")
    return raw


def _parse_prompt(raw: object, label: str) -> str:
    if not isinstance(raw, str) or raw.strip() == "":
        raise RulesError(f"{label}: prompt must be a non-empty string")
    return raw.strip()


def _parse_group(raw: object, label: str) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str) or raw.strip() == "":
        raise RulesError(f"{label}: group must be a non-empty string")
    return raw.strip()


def _parse_parallel(raw: Mapping[str, Any], group: str | None, label: str) -> int:
    if "parallel" not in raw:
        return 1
    if group is None:
        raise RulesError(f"{label}: parallel requires group")
    value = raw["parallel"]
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise RulesError(f"{label}: parallel must be a positive integer")
    return value


def _parse_timeout(raw: object, label: str) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw <= 0:
        raise RulesError(f"{label}: timeout must be a positive number")
    return float(raw)


def _parse_retry(raw: object, label: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise RulesError(f"{label}: retry must be a non-negative integer")
    return raw


def _parse_permission_mode(
    raw: Mapping[str, Any],
    agents: Mapping[str, AgentConfig],
    target: str,
    label: str,
) -> str | None:
    if "permission_mode" not in raw or raw["permission_mode"] is None:
        return None
    if agents[target].type != "claude":
        raise RulesError(
            f"{label}: permission_mode is only supported for claude agents"
        )
    value = raw["permission_mode"]
    if not isinstance(value, str) or value.strip() not in PERMISSION_MODES:
        raise RulesError(f"{label}: invalid permission_mode: {value!r}")
    return value.strip()


def _parse_model(raw: object, label: str) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str) or raw.strip() == "":
        raise RulesError(f"{label}: model must be a non-empty string")
    return raw.strip()


def _parse_when(raw: object, label: str) -> RuleWhen:
    if not isinstance(raw, dict):
        raise RulesError(f"{label}: when must be an object")
    _reject_unknown(raw, WHEN_FIELDS, f"{label}: when")

    has_source = "source" in raw
    has_cron = "cron" in raw
    if has_source and has_cron:
        raise RulesError(
            f"{label}: when cannot declare source or types together with cron"
        )
    if not has_source and not has_cron:
        raise RulesError(f"{label}: when must declare either source or cron")

    if has_cron:
        if "types" in raw:
            raise RulesError(
                f"{label}: when cannot declare source or types together with cron"
            )
        return _cron_when(raw, label)

    if "timezone" in raw:
        raise RulesError(f"{label}: timezone is only valid for cron rules")
    source = raw.get("source")
    if not isinstance(source, str) or source.strip() not in NON_CRON_SOURCES:
        raise RulesError(f"{label}: unknown source: {source!r}")
    return RuleWhen(
        source=source.strip(),
        types=_parse_types(raw.get("types"), label),
        cron=None,
        timezone=None,
    )


def _cron_when(raw: Mapping[str, Any], label: str) -> RuleWhen:
    raw_cron = raw.get("cron")
    if not isinstance(raw_cron, str) or raw_cron.strip() == "":
        raise RulesError(f"{label}: invalid cron expression: {raw_cron!r}")
    expression = raw_cron.strip()
    try:
        five_fields = len(expression.split()) == 5
        if not five_fields or not croniter.is_valid(expression):
            raise ValueError(expression)
    except (ValueError, KeyError, TypeError) as exc:
        raise RulesError(f"{label}: invalid cron expression: {expression!r}") from exc

    raw_tz = raw.get("timezone", "UTC")
    if not isinstance(raw_tz, str) or raw_tz.strip() == "":
        raise RulesError(f"{label}: unknown timezone: {raw_tz!r}")
    timezone = raw_tz.strip()
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise RulesError(f"{label}: unknown timezone: {timezone}") from exc

    return RuleWhen(
        source=EventSource.CRON.value,
        types=(),
        cron=expression,
        timezone=timezone,
    )


def _parse_types(raw: object, label: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not raw:
        raise RulesError(f"{label}: types must be a non-empty list of strings")
    types: list[str] = []
    for item in raw:
        if not isinstance(item, str) or item.strip() == "":
            raise RulesError(f"{label}: types must be a non-empty list of strings")
        value = item.strip()
        if value in types:
            raise RulesError(f"{label}: duplicate event type: {value}")
        types.append(value)
    return tuple(types)


def _reject_unknown(
    data: Mapping[str, Any], allowed: frozenset[str], label: str
) -> None:
    extras = sorted(set(data) - allowed)
    if extras:
        raise RulesError(f"{label}: unknown field(s): {', '.join(extras)}")
