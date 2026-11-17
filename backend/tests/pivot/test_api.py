"""The pivot serve HTTP API: auth, rules hot reload, tickets, webhook."""

import json
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from pivot.api import MAX_RULES_FILE_BYTES, create_serve_app
from pivot.client import SpawnResult
from pivot.config import PivotConfig
from pivot.engine import Engine
from pivot.errors import PivotClientError
from pivot.model import TicketState
from pivot.rules import parse_rules
from pivot.store import PivotStore

TOKEN = "test-token"


def rules_payload(
    rule_name: str = "demo-rule", event_type: str = "demo"
) -> dict[str, Any]:
    return {
        "version": 1,
        "agents": {"claude": {"type": "claude"}},
        "rules": [
            {
                "name": rule_name,
                "when": {"source": "manual", "types": [event_type]},
                "to": "claude",
                "cwd": "/tmp/project",
                "worktree": "new",
                "prompt": "请分析这个任务。",
            }
        ],
    }


class StubPivot:
    def __init__(self) -> None:
        self.active_sessions = 0
        self.spawn_error: Exception | None = None
        self.spawns: list[dict[str, Any]] = []
        self.renames: list[tuple[str, str]] = []
        self.sent: list[tuple[str, str, bool]] = []
        self.stopped: list[str] = []
        self.deleted: list[str] = []

    def active_session_count(self) -> int:
        return self.active_sessions

    def spawn(
        self,
        directory: str,
        agent: str,
        metadata: dict[str, Any] | None = None,
        worktree: dict[str, Any] | None = None,
    ) -> SpawnResult:
        self.spawns.append(
            {
                "directory": directory,
                "agent": agent,
                "metadata": metadata,
                "worktree": worktree,
            }
        )
        if self.spawn_error is not None:
            raise self.spawn_error
        return SpawnResult("inst-1", None, None)

    def rename_instance(self, instance_id: str, name: str) -> Any:
        self.renames.append((instance_id, name))
        return {"agent_instance_id": instance_id, "name": name}

    def send_message(
        self, instance_id: str, content: str, *, mark_as_read: bool = False
    ) -> Any:
        self.sent.append((instance_id, content, mark_as_read))
        return {"id": "msg-1"}

    def stop_instance(self, instance_id: str) -> bool:
        self.stopped.append(instance_id)
        return True

    def delete_instance(self, instance_id: str) -> bool:
        self.deleted.append(instance_id)
        return True


@pytest.fixture
def api(tmp_path):
    payload = rules_payload()
    payload["rules"].append(
        {
            "name": "hook-rule",
            "when": {"source": "webhook", "types": ["demo"]},
            "to": "claude",
            "cwd": "/tmp/project",
            "worktree": "new",
        }
    )
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(json.dumps(payload), encoding="utf-8")
    token_path = tmp_path / "token"
    token_path.write_text(TOKEN + "\n", encoding="utf-8")

    store = PivotStore(tmp_path / "store.db", data_dir=tmp_path)
    pivot = StubPivot()
    config = PivotConfig(
        data_dir=tmp_path,
        rules_path=rules_path,
        store_path=tmp_path / "store.db",
        token_path=token_path,
        daemon_url="http://127.0.0.1:8765",
    )
    engine = Engine(config, store, parse_rules(payload), pivot)
    app = create_serve_app(config, engine)

    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client,
            engine=engine,
            store=store,
            pivot=pivot,
            config=config,
            rules_path=rules_path,
            tmp_path=tmp_path,
        )
    store.close()


def auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def test_requires_bearer(api) -> None:
    assert api.client.get("/v1/rules").status_code == 401
    assert (
        api.client.get(
            "/v1/rules", headers={"Authorization": "Bearer wrong"}
        ).status_code
        == 401
    )
    assert api.client.get("/v1/rules", headers=auth()).status_code == 200


def test_healthz_is_unauthenticated(api) -> None:
    response = api.client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_get_rules_shape(api) -> None:
    response = api.client.get("/v1/rules", headers=auth())

    assert response.status_code == 200
    rules = response.json()["rules"]
    assert [rule["name"] for rule in rules] == ["demo-rule", "hook-rule"]
    assert rules[0]["when"] == {
        "source": "manual",
        "types": ["demo"],
        "cron": None,
        "timezone": "UTC",
    }


def test_get_rules_file(api) -> None:
    response = api.client.get("/v1/rules/file", headers=auth())

    assert response.status_code == 200
    body = response.json()
    assert body["path"] == str(api.rules_path)
    assert json.loads(body["content"])["rules"][0]["name"] == "demo-rule"


def test_get_rules_file_missing(api) -> None:
    api.rules_path.unlink()

    response = api.client.get("/v1/rules/file", headers=auth())

    assert response.status_code == 404
    assert "rules file not found" in response.json()["error"]


def test_save_rules_file_hot_reloads(api) -> None:
    updated = rules_payload(rule_name="renamed", event_type="renamed-type")

    response = api.client.post(
        "/v1/rules/file",
        headers=auth(),
        json={"content": json.dumps(updated)},
    )

    assert response.status_code == 200
    assert [rule["name"] for rule in response.json()["rules"]] == ["renamed"]
    assert response.json()["path"] == str(api.rules_path)
    assert [rule.name for rule in api.engine.rules.rules] == ["renamed"]
    on_disk = json.loads(api.rules_path.read_text(encoding="utf-8"))
    assert on_disk["rules"][0]["name"] == "renamed"
    audit = api.store.list_audit()
    assert audit[0].action == "rules.updated"
    assert audit[0].detail == "1 rule(s)"


def test_save_rules_rejects_invalid_json_without_touching_state(api) -> None:
    original = api.rules_path.read_bytes()
    before = [rule.name for rule in api.engine.rules.rules]

    response = api.client.post(
        "/v1/rules/file",
        headers=auth(),
        json={"content": '{"version": 1,'},
    )

    assert response.status_code == 400
    error = response.json()["error"]
    assert "invalid JSON" in error
    assert ".rules-" not in error
    assert api.rules_path.read_bytes() == original
    assert [rule.name for rule in api.engine.rules.rules] == before
    assert api.store.list_audit() == []


def test_save_rules_rejects_unknown_fields(api) -> None:
    payload = rules_payload()
    payload["rules"][0]["surprise"] = True

    response = api.client.post(
        "/v1/rules/file",
        headers=auth(),
        json={"content": json.dumps(payload)},
    )

    assert response.status_code == 400
    assert "unknown field" in response.json()["error"]


def test_save_rules_rejects_oversized_file(api) -> None:
    response = api.client.post(
        "/v1/rules/file",
        headers=auth(),
        json={"content": "a" * (MAX_RULES_FILE_BYTES + 1)},
    )

    assert response.status_code == 400
    assert "limit" in response.json()["error"]


def test_rules_save_applies_to_the_next_dispatch(api) -> None:
    updated = rules_payload(rule_name="new-rule", event_type="new-type")
    api.client.post(
        "/v1/rules/file", headers=auth(), json={"content": json.dumps(updated)}
    )

    old = api.client.post("/v1/tickets", headers=auth(), json={"type": "demo"})
    new = api.client.post("/v1/tickets", headers=auth(), json={"type": "new-type"})

    assert old.json()["status"] == "no_rule"
    assert new.json()["status"] == "running"


def test_dispatch_and_ticket_detail(api) -> None:
    response = api.client.post(
        "/v1/tickets",
        headers=auth(),
        json={
            "type": "demo",
            "subject": "Investigate",
            "summary": "Short summary",
            "detail": "Full detail",
            "link": "https://example.com",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "running"
    ticket = body["ticket"]
    assert ticket["state"] == TicketState.RUNNING.value
    assert ticket["title"] == "Investigate"
    assert ticket["agent_instance_id"] == "inst-1"

    detail = api.client.get(f"/v1/tickets/{ticket['id']}", headers=auth())
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["ticket"]["id"] == ticket["id"]
    assert payload["event"]["type"] == "demo"
    assert payload["event"]["summary"] == "Short summary"
    assert payload["event"]["detail"] == "Full detail"
    assert [run["status"] for run in payload["runs"]] == ["running"]
    assert [entry["action"] for entry in payload["audit"]] == ["ticket.dispatched"]
    assert payload["receipt"] is None

    missing = api.client.get("/v1/tickets/tkt-missing", headers=auth())
    assert missing.status_code == 404


def test_duplicate_key_is_reported(api) -> None:
    first = api.client.post(
        "/v1/tickets", headers=auth(), json={"type": "demo", "key": "same"}
    )
    second = api.client.post(
        "/v1/tickets", headers=auth(), json={"type": "demo", "key": "same"}
    )

    assert first.json()["status"] == "running"
    assert second.json()["status"] == "duplicate"
    assert second.json()["event"] == first.json()["event"]
    assert "ticket" not in second.json()


def test_no_rule_is_reported(api) -> None:
    response = api.client.post("/v1/tickets", headers=auth(), json={"type": "unknown"})

    assert response.status_code == 200
    assert response.json()["status"] == "no_rule"
    assert api.store.list_tickets() == []


def test_dispatch_failure_returns_500(api) -> None:
    api.pivot.spawn_error = PivotClientError("daemon exploded")

    response = api.client.post(
        "/v1/tickets", headers=auth(), json={"type": "demo", "subject": "Boom"}
    )

    assert response.status_code == 500
    body = response.json()
    assert body["status"] == "failed"
    assert "daemon exploded" in body["error"]
    assert body["ticket"]["state"] == TicketState.FAILED.value


def test_list_tickets_filters_and_limits(api) -> None:
    api.client.post(
        "/v1/tickets", headers=auth(), json={"type": "demo", "subject": "one"}
    )
    api.client.post(
        "/v1/tickets", headers=auth(), json={"type": "demo", "subject": "two"}
    )

    limited = api.client.get("/v1/tickets?limit=1", headers=auth())
    assert limited.status_code == 200
    assert len(limited.json()["tickets"]) == 1

    running = api.client.get("/v1/tickets?state=running", headers=auth())
    assert len(running.json()["tickets"]) == 2

    done = api.client.get("/v1/tickets?state=done", headers=auth())
    assert done.json()["tickets"] == []

    assert api.client.get("/v1/tickets?state=banana", headers=auth()).status_code == 400
    assert api.client.get("/v1/tickets?limit=0", headers=auth()).status_code == 400


def test_delete_ticket_is_idempotent(api) -> None:
    created = api.client.post(
        "/v1/tickets", headers=auth(), json={"type": "demo"}
    ).json()
    ticket_id = created["ticket"]["id"]

    deleted = api.client.delete(f"/v1/tickets/{ticket_id}", headers=auth())

    assert deleted.status_code == 200
    body = deleted.json()
    assert body == {
        "id": ticket_id,
        "deleted": True,
        "session_deleted": True,
        "session_error": None,
    }
    assert api.pivot.stopped == ["inst-1"]
    assert api.pivot.deleted == ["inst-1"]
    assert api.client.get(f"/v1/tickets/{ticket_id}", headers=auth()).status_code == 404
    assert (
        api.client.delete(f"/v1/tickets/{ticket_id}", headers=auth()).status_code == 404
    )


def test_webhook_dispatch_and_dedupe(api) -> None:
    headers = {**auth(), "X-Pivot-Key": "hook-1"}
    first = api.client.post(
        "/webhook", headers=headers, json={"type": "demo", "subject": "From hook"}
    )
    second = api.client.post(
        "/webhook", headers=headers, json={"type": "demo", "subject": "From hook"}
    )

    assert first.status_code == 200
    assert first.json()["status"] == "running"
    assert second.json()["status"] == "duplicate"
    assert api.client.post("/webhook", json={"type": "demo"}).status_code == 401
