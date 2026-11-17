"""Pivot data model: request bodies, rule objects, and stored rows.

The rule objects are frozen dataclasses constructed by ``pivot.rules``; they
carry tuples/mappings so a reloaded rules file can never be mutated in place.
``TicketBody`` is the only Pydantic model here — it validates the one request
body the dispatch API accepts.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, field_validator

DEFAULT_PROMPT = "按输入文件中的要求完成任务。"


class EventSource(str, Enum):
    MANUAL = "manual"
    WEBHOOK = "webhook"
    CRON = "cron"


class TicketState(str, Enum):
    QUEUED = "queued"
    SPAWNING = "spawning"
    RUNNING = "running"
    DONE = "done"
    NEEDS_HUMAN = "needs_human"
    FAILED = "failed"
    TAKEN_OVER = "taken_over"
    INTERRUPTED = "interrupted"


ACTIVE_TICKET_STATES = frozenset(
    {
        TicketState.SPAWNING.value,
        TicketState.RUNNING.value,
        TicketState.NEEDS_HUMAN.value,
    }
)


class TicketBody(BaseModel):
    """The dispatch request body. Unknown fields are rejected (422)."""

    model_config = ConfigDict(extra="forbid")

    type: str
    subject: str | None = None
    summary: str | None = None
    detail: str | None = None
    link: str | None = None
    key: str | None = None
    hops: int = 0

    @field_validator("type")
    @classmethod
    def _type_must_not_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if stripped == "":
            raise ValueError("type must not be blank")
        return stripped

    @field_validator("hops", mode="before")
    @classmethod
    def _hops_must_be_non_negative_int(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("hops must be a non-negative integer")
        if value < 0:
            raise ValueError("hops must be a non-negative integer")
        return value


@dataclass(frozen=True)
class AgentConfig:
    """One entry under ``agents`` in rules.json."""

    type: str


@dataclass(frozen=True)
class RuleWhen:
    """A validated trigger: either source+types, or cron+timezone."""

    source: str | None
    types: tuple[str, ...]
    cron: str | None
    timezone: str | None

    @property
    def is_cron(self) -> bool:
        return self.cron is not None


@dataclass(frozen=True)
class Rule:
    """One fully validated rule entry."""

    name: str
    when: RuleWhen
    to: str
    cwd: str
    worktree: str
    read_only: bool
    prompt: str
    group: str | None
    parallel: int
    timeout: float | None
    retry: int
    permission_mode: str | None
    model: str | None

    def matches(self, source: str, event_type: str) -> bool:
        if self.when.is_cron or self.when.source != source:
            return False
        return not self.when.types or event_type in self.when.types


@dataclass(frozen=True)
class RulesFile:
    """The whole rules file: agents, ordered rules, and payload helpers."""

    version: int
    agents: Mapping[str, AgentConfig]
    rules: tuple[Rule, ...]

    def agent_type(self, name: str) -> str | None:
        agent = self.agents.get(name)
        return None if agent is None else agent.type

    def match(self, source: str, event_type: str) -> Rule | None:
        """First rule in file order that matches — later rules never win."""
        for rule in self.rules:
            if rule.matches(source, event_type):
                return rule
        return None

    def group_members(self, group: str) -> tuple[Rule, ...]:
        return tuple(rule for rule in self.rules if rule.group == group)

    def rule_payload(self, rule: Rule) -> dict[str, Any]:
        """The API shape of one rule (see §2.2 of the upgrade guide)."""
        payload: dict[str, Any] = {
            "name": rule.name,
            "when": {
                "source": rule.when.source,
                "types": list(rule.when.types),
                "cron": rule.when.cron,
                "timezone": rule.when.timezone or "UTC",
            },
            "to": rule.to,
            "cwd": rule.cwd,
            "worktree": rule.worktree,
            "read_only": rule.read_only,
            "prompt": rule.prompt,
            "parallel": rule.parallel,
            "retry": rule.retry,
        }
        if rule.group is not None:
            payload["group"] = rule.group
        if rule.timeout is not None:
            payload["timeout"] = rule.timeout
        if rule.permission_mode is not None:
            payload["permission_mode"] = rule.permission_mode
        if rule.model is not None:
            payload["model"] = rule.model
        return payload

    def rules_payload(self) -> list[dict[str, Any]]:
        return [self.rule_payload(rule) for rule in self.rules]


@dataclass(frozen=True)
class StoredEvent:
    id: str
    source: str
    source_key: str
    event_type: str
    subject: str | None
    summary: str | None
    detail: str | None
    link: str | None
    hops: int
    payload: dict[str, Any] | None
    created_at: str


@dataclass(frozen=True)
class StoredTicket:
    id: str
    event_id: str
    state: str
    rule: str
    title: str
    cwd: str
    agent: str
    worktree_path: str | None
    agent_instance_id: str | None
    needs_human_since: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class StoredRun:
    id: str
    ticket_id: str
    attempt: int
    status: str
    error: str | None
    started_at: str
    finished_at: str | None


@dataclass(frozen=True)
class AuditEntry:
    id: int
    ticket_id: str | None
    actor: str
    action: str
    detail: str | None
    created_at: str
