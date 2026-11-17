"""rules.json parsing: strict validation, cron/timezone, payload shapes."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from pivot.model import RulesFile
from pivot.rules import DEFAULT_PROMPT, RulesError, load_rules, parse_rules


def valid_payload() -> dict[str, Any]:
    return {
        "version": 1,
        "agents": {"claude": {"type": "claude"}},
        "rules": [
            {
                "name": "wdc-pdc-diff",
                "when": {"source": "manual", "types": ["wdc-pdc.diff"]},
                "to": "claude",
                "cwd": "/tmp/project",
                "worktree": "new",
            },
            {
                "name": "nightly-check",
                "when": {"cron": "0 9 * * 1-5", "timezone": "Asia/Shanghai"},
                "to": "claude",
                "cwd": "~/project",
                "worktree": "none",
                "read_only": True,
            },
        ],
    }


def parse_ok(payload: dict[str, Any]) -> RulesFile:
    return parse_rules(payload)


def test_parses_manual_and_cron_rules() -> None:
    rules = parse_ok(valid_payload())

    assert [rule.name for rule in rules.rules] == ["wdc-pdc-diff", "nightly-check"]
    manual, cron = rules.rules
    assert manual.when.source == "manual"
    assert manual.when.types == ("wdc-pdc.diff",)
    assert manual.when.cron is None
    assert manual.when.timezone is None
    assert cron.when.source == "cron"
    assert cron.when.types == ()
    assert cron.when.cron == "0 9 * * 1-5"
    assert cron.when.timezone == "Asia/Shanghai"


def test_defaults_are_applied() -> None:
    rule = parse_ok(valid_payload()).rules[0]

    assert rule.prompt == DEFAULT_PROMPT
    assert rule.read_only is False
    assert rule.group is None
    assert rule.parallel == 1
    assert rule.timeout is None
    assert rule.retry == 0
    assert rule.permission_mode is None
    assert rule.model is None


def test_read_only_claude_gets_auto_permission() -> None:
    cron = parse_ok(valid_payload()).rules[1]

    assert cron.read_only is True
    assert cron.permission_mode == "auto"


def test_explicit_permission_mode_wins() -> None:
    payload = valid_payload()
    payload["rules"][1]["permission_mode"] = "plan"

    assert parse_ok(payload).rules[1].permission_mode == "plan"


def test_payload_shapes() -> None:
    rules = parse_ok(valid_payload())

    manual = rules.rule_payload(rules.rules[0])
    cron = rules.rule_payload(rules.rules[1])

    assert manual["when"] == {
        "source": "manual",
        "types": ["wdc-pdc.diff"],
        "cron": None,
        "timezone": "UTC",
    }
    assert cron["when"] == {
        "source": "cron",
        "types": [],
        "cron": "0 9 * * 1-5",
        "timezone": "Asia/Shanghai",
    }
    assert manual["name"] == "wdc-pdc-diff"
    assert manual["prompt"] == DEFAULT_PROMPT
    assert "permission_mode" not in manual
    assert cron["permission_mode"] == "auto"


def test_rules_payload_returns_every_rule() -> None:
    rules = parse_ok(valid_payload())

    payload = rules.rules_payload()

    assert [item["name"] for item in payload] == ["wdc-pdc-diff", "nightly-check"]


def test_first_matching_rule_wins() -> None:
    payload = valid_payload()
    payload["rules"].append(
        {
            "name": "second-manual",
            "when": {"source": "manual"},
            "to": "claude",
            "cwd": "/tmp/project",
            "worktree": "new",
        }
    )
    rules = parse_ok(payload)

    assert rules.match("manual", "wdc-pdc.diff").name == "wdc-pdc-diff"
    assert rules.match("manual", "other").name == "second-manual"
    assert rules.match("webhook", "anything") is None


def test_rule_without_types_matches_every_type() -> None:
    payload = valid_payload()
    payload["rules"] = [payload["rules"][1]]
    payload["rules"][0]["when"] = {"source": "webhook"}
    rules = parse_ok(payload)

    assert rules.match("webhook", "anything").name == "nightly-check"
    assert rules.match("manual", "anything") is None


def test_group_members_follow_file_order() -> None:
    payload = valid_payload()
    payload["rules"][0]["group"] = "review"
    payload["rules"][1]["group"] = "review"
    rules = parse_ok(payload)

    assert [rule.name for rule in rules.group_members("review")] == [
        "wdc-pdc-diff",
        "nightly-check",
    ]
    assert rules.group_members("missing") == ()


def test_load_rules_reads_file(tmp_path: Path) -> None:
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(valid_payload()), encoding="utf-8")

    assert load_rules(path).rules[0].name == "wdc-pdc-diff"


def test_load_rules_reports_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "rules.json"
    path.write_text('{"version": 1,', encoding="utf-8")

    with pytest.raises(RulesError, match="invalid JSON"):
        load_rules(path)


def test_load_rules_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_rules(tmp_path / "missing.json")


def _mutate(case: dict[str, Any]) -> dict[str, Any]:
    payload = valid_payload()
    rule = payload["rules"][0]
    if case.get("target") == "root":
        for key in case.get("remove", []):
            payload.pop(key, None)
        payload.update(case.get("set", {}))
    elif case.get("target") == "rule":
        for key in case.get("remove", []):
            rule.pop(key, None)
        rule.update(case.get("set", {}))
    elif case.get("target") == "when":
        for key in case.get("remove", []):
            rule["when"].pop(key, None)
        rule["when"].update(case.get("set", {}))
    elif case.get("target") == "agents":
        payload["agents"] = case.get("set", {})
    return payload


INVALID_CASES = [
    ({"target": "root", "remove": ["version"]}, "version"),
    ({"target": "root", "set": {"version": 2}}, "version"),
    ({"target": "root", "set": {"extra": True}}, "unknown field"),
    ({"target": "root", "remove": ["agents"]}, "agents must be an object"),
    (
        {"target": "root", "set": {"agents": {"claude": {"kind": "claude"}}}},
        "unknown field",
    ),
    ({"target": "root", "set": {"agents": {"claude": {"type": ""}}}}, "agent type"),
    ({"target": "root", "set": {"rules": {}}}, "rules must be a list"),
    (
        {
            "target": "root",
            "set": {
                "rules": [
                    valid_payload()["rules"][0],
                    copy.deepcopy(valid_payload()["rules"][0]),
                ]
            },
        },
        "duplicate rule name",
    ),
    ({"target": "rule", "set": {"name": ""}}, "name"),
    ({"target": "rule", "set": {"name": 3}}, "name"),
    ({"target": "rule", "set": {"when": []}}, "when must be an object"),
    (
        {"target": "when", "set": {"cron": "0 9 * * *"}},
        "cannot declare source or types",
    ),
    ({"target": "when", "remove": ["source", "types"]}, "source or cron"),
    ({"target": "when", "set": {"source": "email"}}, "unknown source"),
    ({"target": "when", "set": {"types": "a"}}, "types must be a non-empty list"),
    ({"target": "when", "set": {"types": []}}, "types must be a non-empty list"),
    ({"target": "when", "set": {"types": ["a", "a"]}}, "duplicate event type"),
    ({"target": "when", "set": {"types": ["a", 1]}}, "types must be a non-empty list"),
    ({"target": "when", "set": {"timezone": "UTC"}}, "timezone is only valid"),
    (
        {
            "target": "when",
            "set": {"cron": "not a cron"},
            "remove": ["source", "types"],
        },
        "invalid cron expression",
    ),
    (
        {
            "target": "when",
            "set": {"cron": "0 9 * * *", "timezone": "Mars/Base"},
            "remove": ["source", "types"],
        },
        "unknown timezone",
    ),
    ({"target": "rule", "set": {"to": "nobody"}}, "unknown agent"),
    ({"target": "rule", "set": {"to": ""}}, "unknown agent"),
    ({"target": "rule", "set": {"cwd": "relative/path"}}, "cwd must be absolute"),
    ({"target": "rule", "set": {"cwd": ""}}, "cwd"),
    ({"target": "rule", "set": {"worktree": "auto"}}, "worktree"),
    (
        {
            "target": "rule",
            "set": {"worktree": "none", "read_only": False},
        },
        "requires read_only",
    ),
    ({"target": "rule", "set": {"read_only": "yes"}}, "read_only"),
    ({"target": "rule", "set": {"prompt": "  "}}, "prompt"),
    ({"target": "rule", "set": {"group": ""}}, "group"),
    ({"target": "rule", "set": {"parallel": 2}}, "parallel requires group"),
    (
        {"target": "rule", "set": {"group": "g", "parallel": 0}},
        "parallel must be a positive integer",
    ),
    ({"target": "rule", "set": {"timeout": 0}}, "timeout must be a positive number"),
    ({"target": "rule", "set": {"timeout": "5"}}, "timeout must be a positive number"),
    ({"target": "rule", "set": {"retry": -1}}, "retry must be a non-negative integer"),
    (
        {"target": "rule", "set": {"retry": True}},
        "retry must be a non-negative integer",
    ),
    ({"target": "rule", "set": {"permission_mode": "yolo"}}, "invalid permission_mode"),
    (
        {
            "target": "root",
            "set": {
                "agents": {
                    "claude": {"type": "claude"},
                    "codex": {"type": "codex"},
                }
            },
        },
        "only supported for claude",
    ),
    ({"target": "rule", "set": {"model": ""}}, "model"),
    ({"target": "rule", "set": {"surprise": 1}}, "unknown field"),
]


@pytest.mark.parametrize(("case", "needle"), INVALID_CASES)
def test_invalid_rules(case: dict[str, Any], needle: str) -> None:
    if needle == "only supported for claude":
        payload = _mutate(case)
        payload["rules"][0]["to"] = "codex"
        payload["rules"][0]["permission_mode"] = "auto"
    else:
        payload = _mutate(case)

    with pytest.raises(RulesError, match=needle):
        parse_rules(payload)


def test_parse_rules_rejects_non_object() -> None:
    with pytest.raises(RulesError, match="must be a JSON object"):
        parse_rules([])
