"""Local server REST subset: nonce auth + backend-shape parity.

Covers the renderer subset (instances list/detail, message post, machines)
and the SDK subset the headless agent calls (register, agent message,
pending, status, user message, request-input, end session).
"""

import hashlib
import hmac
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import pytest
from starlette.testclient import TestClient

from vicoa.local_server.app import (
    LOCAL_FREE_AGENT_LIMIT,
    LOCAL_SUBSCRIPTION_ID,
    LOCAL_USER_ID,
    create_local_app,
)
from vicoa.local_server.store import LocalStore
from vicoa.rpc import file_ops
from vicoa.terminal.service import TerminalService

NONCE = "0123456789abcdef0123456789abcdef"
ORIGIN = "http://localhost:3000"
AUTH = {"Authorization": f"Bearer {NONCE}"}


class StubDaemon:
    """The slice of MachineDaemon the local app consumes."""

    machine_id: str | None = None

    def _handle_rpc_request(self, frame: dict) -> dict:
        if frame.get("method") == "read-file":
            return file_ops.read_file(**frame["params"])
        return {"echoed_method": frame.get("method")}

    def _supported_rpc_methods(self) -> list[str]:
        return [
            "spawn-session",
            "list-files",
            "read-file",
            "git-status",
            "git-diff",
            "git-worktree-list",
            "git-worktree-remove",
        ]

    def _detect_available_agents(self) -> dict[str, bool]:
        return {"claude": True, "codex": False}

    def _capabilities(self) -> list[str]:
        return ["worktree"]


@pytest.fixture
def store(tmp_path: Path) -> Iterator[LocalStore]:
    s = LocalStore(tmp_path / "store.db")
    yield s
    s.close()


@pytest.fixture
def client(store: LocalStore) -> Iterator[TestClient]:
    app = create_local_app(
        daemon=StubDaemon(),
        store=store,
        nonce=NONCE,
        allowed_origin=ORIGIN,
        local_only=True,
        terminal=TerminalService(),
    )
    with TestClient(app) as test_client:
        yield test_client


def _register(client: TestClient, instance_id: str | None = None) -> str:
    payload: dict = {"agent_type": "Claude Code", "project": "/tmp/proj"}
    if instance_id:
        payload["agent_instance_id"] = instance_id
    response = client.post("/api/v1/agent-instances", json=payload, headers=AUTH)
    assert response.status_code == 201, response.text
    return response.json()["agent_instance_id"]


# ----------------------------------------------------------------------
# Health + auth
# ----------------------------------------------------------------------
def test_healthz_needs_no_auth(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "mode": "local-only",
        "machine_id": "local",
        # No cloud link exists in local-only mode; the tray reads null as
        # "nothing to report" (vs connecting/connected/auth_failed in cloud
        # mode).
        "cloud": None,
    }


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/agent-instances",
        "/api/v1/machines",
        "/api/v1/messages/pending?agent_instance_id=x",
        "/api/v1/files",
        "/api/v1/commands/claude",
        "/api/v1/commands",
        "/api/v1/agent-catalog",
        "/api/v1/machines/local/agent-models",
        "/api/v1/billing/subscription",
    ],
)
def test_rest_rejects_missing_bearer(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 401


def test_rest_rejects_wrong_nonce(client: TestClient) -> None:
    response = client.get(
        "/api/v1/agent-instances", headers={"Authorization": "Bearer wrong"}
    )
    assert response.status_code == 401


# ----------------------------------------------------------------------
# SDK subset
# ----------------------------------------------------------------------
def test_register_shape_and_conflict(client: TestClient) -> None:
    instance_id = "33333333-3333-3333-3333-333333333333"
    response = client.post(
        "/api/v1/agent-instances",
        json={
            "agent_type": "Claude Code",
            "agent_instance_id": instance_id,
            "transport": "ws",
            "source": "app",
            "session_config": {"agent": "claude"},
        },
        headers=AUTH,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["agent_instance_id"] == instance_id
    assert body["status"] == "ACTIVE"
    assert body["machine_id"] == "local"
    assert body["session_config"] == {"agent": "claude"}
    assert body["instance_metadata"] == {"transport": "ws", "source": "app"}
    assert body["agent_type_name"] == "Claude Code"

    duplicate = client.post(
        "/api/v1/agent-instances",
        json={"agent_type": "Claude Code", "agent_instance_id": instance_id},
        headers=AUTH,
    )
    assert duplicate.status_code == 409


def test_agent_message_returns_queued_user_messages(client: TestClient) -> None:
    instance_id = _register(client)
    # Renderer posts a user message; it must stay pending for the agent.
    posted = client.post(
        f"/api/v1/agent-instances/{instance_id}/messages",
        json={"content": "do the thing"},
        headers=AUTH,
    )
    assert posted.status_code == 200
    message = posted.json()
    assert message["sender_type"] == "USER"
    assert message["sender_user_id"] == LOCAL_USER_ID
    assert message["created_at"].endswith("Z")

    agent_reply = client.post(
        "/api/v1/messages/agent",
        json={
            "agent_instance_id": instance_id,
            "content": "on it",
            "requires_user_input": False,
        },
        headers=AUTH,
    )
    assert agent_reply.status_code == 200
    body = agent_reply.json()
    assert body["success"] is True
    assert body["agent_instance_id"] == instance_id
    assert [m["content"] for m in body["queued_user_messages"]] == ["do the thing"]
    # SDK-flavor MessageResponse (servers.api.models): no sender_user_* keys.
    assert set(body["queued_user_messages"][0]) == {
        "id",
        "content",
        "sender_type",
        "created_at",
        "requires_user_input",
        "message_metadata",
    }


def test_pending_flow_and_stale(client: TestClient) -> None:
    instance_id = _register(client)
    client.post(
        f"/api/v1/agent-instances/{instance_id}/messages",
        json={"content": "ping"},
        headers=AUTH,
    )
    pending = client.get(
        "/api/v1/messages/pending",
        params={"agent_instance_id": instance_id, "last_read_message_id": ""},
        headers=AUTH,
    )
    assert pending.status_code == 200
    body = pending.json()
    assert body["status"] == "ok"
    assert [m["content"] for m in body["messages"]] == ["ping"]

    stale = client.get(
        "/api/v1/messages/pending",
        params={
            "agent_instance_id": instance_id,
            "last_read_message_id": "not-the-cursor",
        },
        headers=AUTH,
    )
    assert stale.json()["status"] == "stale"


def test_status_update_shape(client: TestClient) -> None:
    instance_id = _register(client)
    response = client.put(
        f"/api/v1/agent-instances/{instance_id}/status",
        json={"status": "AWAITING_INPUT"},
        headers=AUTH,
    )
    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "instance_id": instance_id,
        "status": "AWAITING_INPUT",
    }
    assert (
        client.put(
            f"/api/v1/agent-instances/{instance_id}/status",
            json={"status": "NOT_A_STATUS"},
            headers=AUTH,
        ).status_code
        == 400
    )
    assert (
        client.put(
            "/api/v1/agent-instances/44444444-4444-4444-4444-444444444444/status",
            json={"status": "ACTIVE"},
            headers=AUTH,
        ).status_code
        == 404
    )


def test_user_message_endpoint_marks_read(client: TestClient) -> None:
    instance_id = _register(client)
    response = client.post(
        "/api/v1/messages/user",
        json={
            "agent_instance_id": instance_id,
            "content": "noted",
            "mark_as_read": True,
        },
        headers=AUTH,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True and body["marked_as_read"] is True
    # Marked read on arrival: not pending.
    pending = client.get(
        "/api/v1/messages/pending",
        params={"agent_instance_id": instance_id},
        headers=AUTH,
    )
    assert pending.json()["messages"] == []


def test_request_input_and_end_session(client: TestClient) -> None:
    instance_id = _register(client)
    reply = client.post(
        "/api/v1/messages/agent",
        json={"agent_instance_id": instance_id, "content": "need input?"},
        headers=AUTH,
    ).json()
    marked = client.patch(
        f"/api/v1/messages/{reply['message_id']}/request-input", headers=AUTH
    )
    assert marked.status_code == 200
    body = marked.json()
    assert body["agent_instance_id"] == instance_id
    assert body["messages"] == [] and body["status"] == "ok"

    ended = client.post(
        "/api/v1/sessions/end",
        json={"agent_instance_id": instance_id},
        headers=AUTH,
    )
    assert ended.status_code == 200
    assert ended.json() == {
        "success": True,
        "agent_instance_id": instance_id,
        "final_status": "COMPLETED",
    }


# ----------------------------------------------------------------------
# Renderer subset
# ----------------------------------------------------------------------
def test_instance_list_and_detail_shapes(client: TestClient) -> None:
    instance_id = _register(client)
    client.post(
        f"/api/v1/agent-instances/{instance_id}/messages",
        json={"content": "hello there"},
        headers=AUTH,
    )

    listed = client.get("/api/v1/agent-instances", headers=AUTH)
    assert listed.status_code == 200
    page = listed.json()
    assert page["total"] == 1 and page["has_more"] is False
    item = page["items"][0]
    assert item["id"] == instance_id
    assert item["latest_message"] == "hello there"
    assert item["chat_length"] == 1
    assert item["machine_id"] == "local"

    detail = client.get(
        f"/api/v1/agent-instances/{instance_id}",
        params={"message_limit": 50},
        headers=AUTH,
    )
    assert detail.status_code == 200
    body = detail.json()
    # The exact field set the chat page's AgentInstanceDetail parser reads.
    assert {
        "id",
        "agent_type_id",
        "agent_type_name",
        "name",
        "status",
        "started_at",
        "ended_at",
        "git_diff",
        "messages",
        "last_read_message_id",
        "last_heartbeat_at",
        "access_level",
        "is_owner",
        "instance_metadata",
        "session_config",
        "project",
        "home_dir",
        "machine_id",
    } <= set(body)
    assert body["is_owner"] is True and body["access_level"] == "WRITE"
    assert [m["content"] for m in body["messages"]] == ["hello there"]

    missing = client.get(
        "/api/v1/agent-instances/55555555-5555-5555-5555-555555555555",
        headers=AUTH,
    )
    assert missing.status_code == 404


def test_detail_message_pagination_cursor(client: TestClient) -> None:
    instance_id = _register(client)
    for index in range(3):
        client.post(
            f"/api/v1/agent-instances/{instance_id}/messages",
            json={"content": f"m{index}"},
            headers=AUTH,
        )
    tail = client.get(
        f"/api/v1/agent-instances/{instance_id}",
        params={"message_limit": 2},
        headers=AUTH,
    ).json()["messages"]
    assert [m["content"] for m in tail] == ["m1", "m2"]
    older = client.get(
        f"/api/v1/agent-instances/{instance_id}",
        params={"message_limit": 2, "before_message_id": tail[0]["id"]},
        headers=AUTH,
    ).json()["messages"]
    assert [m["content"] for m in older] == ["m0"]


def test_machines_synthetic_row(client: TestClient) -> None:
    response = client.get("/api/v1/machines", headers=AUTH)
    assert response.status_code == 200
    machines = response.json()["machines"]
    assert len(machines) == 1
    machine = machines[0]
    assert machine["machine_id"] == "local"
    assert machine["metadata"]["available_agents"] == {
        "claude": True,
        "codex": False,
    }
    assert machine["metadata"]["capabilities"] == ["worktree"]
    assert machine["last_heartbeat_at"].endswith("Z")
    assert machine["recent_directories"] == []


# ----------------------------------------------------------------------
# Chat-input helpers: @-mention files + slash commands + new-session catalog
# ----------------------------------------------------------------------
def test_file_mentions_lists_project_files(client: TestClient, tmp_path: Path) -> None:
    project = tmp_path / "proj"
    (project / "src").mkdir(parents=True)
    (project / "README.md").write_text("hi")
    (project / "src" / "main.py").write_text("print(1)")
    (project / ".gitignore").write_text("ignored.txt\n")
    (project / "ignored.txt").write_text("secret")

    response = client.get(
        "/api/v1/files", params={"project_path": str(project)}, headers=AUTH
    )
    assert response.status_code == 200
    body = response.json()
    assert body["project_path"] == str(project)
    assert body["file_count"] == len(body["files"])
    # Files surface as forward-slash paths relative to the project root.
    assert "README.md" in body["files"]
    assert "src/main.py" in body["files"]
    # Nested dirs surface as folder entries (trailing slash) — the same shape
    # the CLI syncs, so the renderer's parser handles them unchanged.
    assert "src/" in body["files"]
    # .gitignore'd files are excluded (same ignore rules as the cloud sync).
    assert "ignored.txt" not in body["files"]


def test_file_mentions_empty_without_project_path(client: TestClient) -> None:
    response = client.get("/api/v1/files", headers=AUTH)
    assert response.status_code == 200
    assert response.json() == {"project_path": "", "files": [], "file_count": 0}


# ----------------------------------------------------------------------
# Media: the Files tab's video player loads a signed URL off this server
# ----------------------------------------------------------------------
def _read_file(client: TestClient, cwd: str, path: str) -> dict:
    """`read-file` over the local socket, the way the renderer opens a tab."""
    with client.websocket_connect(
        "/ws", subprotocols=["vicoa-ws", f"vicoa-local.{NONCE}"]
    ) as ws:
        ws.send_json({"type": "hello", "scope": "user-scoped"})
        ws.receive_json()
        ws.send_json(
            {
                "type": "rpc-call",
                "request_id": "m1",
                "machine_id": "local",
                "method": "read-file",
                "params": {"cwd": cwd, "path": path},
            }
        )
        return ws.receive_json()["result"]


def _signed(cwd: str, path: str) -> str:
    """A correctly signed media URL for any path, to probe the GET's own checks."""
    sig = hmac.new(
        NONCE.encode(), f"media\0{cwd}\0{path}".encode(), hashlib.sha256
    ).hexdigest()
    return "/api/v1/files/media?" + urlencode({"cwd": cwd, "path": path, "sig": sig})


def test_video_read_file_carries_a_stream_url_with_ranges(
    client: TestClient, tmp_path: Path
) -> None:
    project = tmp_path / "proj"
    (project / "out").mkdir(parents=True)
    (project / "out" / "demo.mp4").write_bytes(bytes(range(256)) * 4)

    result = _read_file(client, str(project), "out/demo.mp4")
    assert result["mime_type"] == "video/mp4"
    url = result["stream_url"]
    # Signed, not the nonce: the URL goes into a <video src>.
    assert url.startswith("/api/v1/files/media?") and NONCE not in url

    whole = client.get(url)
    assert whole.status_code == 200
    assert whole.headers["content-type"] == "video/mp4"
    assert whole.headers["x-content-type-options"] == "nosniff"
    assert len(whole.content) == 1024

    part = client.get(url, headers={"Range": "bytes=256-511"})
    assert part.status_code == 206
    assert part.content == bytes(range(256))


def test_non_video_read_file_has_no_stream_url(
    client: TestClient, tmp_path: Path
) -> None:
    (tmp_path / "notes.txt").write_text("hi")
    assert "stream_url" not in _read_file(client, str(tmp_path), "notes.txt")


def test_media_rejects_a_signature_for_another_file(
    client: TestClient, tmp_path: Path
) -> None:
    (tmp_path / "a.mp4").write_bytes(b"a")
    (tmp_path / "b.mp4").write_bytes(b"b")
    url = _read_file(client, str(tmp_path), "a.mp4")["stream_url"]

    assert client.get(url.replace("a.mp4", "b.mp4")).status_code == 401
    assert client.get(url.replace("sig=", "sig=0")).status_code == 401


def test_media_only_serves_video_inside_the_project(
    client: TestClient, tmp_path: Path
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    (tmp_path / "outside.mp4").write_bytes(b"x")
    (project / "page.html").write_text("<script>alert(1)</script>")
    (project / "clip.mp4").symlink_to(project / "page.html")
    cwd = str(project)

    assert client.get(_signed(cwd, "../outside.mp4")).status_code == 403
    assert client.get(_signed(cwd, "page.html")).status_code == 415
    # A video-named link to something else is judged by its target.
    assert client.get(_signed(cwd, "clip.mp4")).status_code == 415
    assert client.get(_signed(cwd, "missing.mp4")).status_code == 404


def test_slash_commands_claude_shape(client: TestClient) -> None:
    response = client.get("/api/v1/commands/claude", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["agent_type"] == "claude"
    assert isinstance(body["commands"], list)
    for command in body["commands"]:
        assert {"name", "description", "kind"} <= set(command)
        assert set(command) <= {"name", "description", "kind", "insert"}
        assert isinstance(command["name"], str)
        assert isinstance(command["description"], str)
        assert command["kind"] in {"command", "skill"}


def test_slash_commands_agent_without_source_is_empty(client: TestClient) -> None:
    # Codex now has a local source (~/.codex, ~/.agents); opencode does not.
    response = client.get("/api/v1/commands/opencode", headers=AUTH)
    assert response.status_code == 200
    assert response.json() == {"agent_type": "opencode", "commands": []}


def test_slash_commands_list_shape(client: TestClient) -> None:
    response = client.get("/api/v1/commands", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    for entry in body:
        assert set(entry) == {"agent_type", "commands"}
        assert isinstance(entry["commands"], list)
    # An agent without a local source scans empty, so the list is empty.
    filtered = client.get(
        "/api/v1/commands", params={"agent_type": "opencode"}, headers=AUTH
    )
    assert filtered.status_code == 200
    assert filtered.json() == []


def test_agent_catalog_served_with_etag(client: TestClient) -> None:
    response = client.get("/api/v1/agent-catalog", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body.get("agents"), list) and body["agents"]
    etag = response.headers.get("etag")
    assert etag
    # Conditional GET short-circuits to 304 (renderer caches the catalog).
    cached = client.get(
        "/api/v1/agent-catalog", headers={**AUTH, "If-None-Match": etag}
    )
    assert cached.status_code == 304


def test_machine_agent_models_empty(client: TestClient) -> None:
    response = client.get("/api/v1/machines/local/agent-models", headers=AUTH)
    assert response.status_code == 200
    assert response.json() == {"agent_models": {}}


# ----------------------------------------------------------------------
# Delete session + auth/billing stubs
# ----------------------------------------------------------------------
def test_delete_instance_removes_instance_and_messages(client: TestClient) -> None:
    instance_id = _register(client)
    client.post(
        f"/api/v1/agent-instances/{instance_id}/messages",
        json={"content": "hello"},
        headers=AUTH,
    )
    # Present before delete.
    assert client.get("/api/v1/agent-instances", headers=AUTH).json()["total"] == 1

    deleted = client.delete(f"/api/v1/agent-instances/{instance_id}", headers=AUTH)
    assert deleted.status_code == 200
    assert deleted.json() == {"message": "Agent instance deleted successfully"}

    # Gone from the list and its detail 404s; messages are wiped.
    listed = client.get("/api/v1/agent-instances", headers=AUTH).json()
    assert listed["total"] == 0 and listed["items"] == []
    assert (
        client.get(f"/api/v1/agent-instances/{instance_id}", headers=AUTH).status_code
        == 404
    )

    # Idempotent: re-deleting the soft-deleted row still returns 200 (cloud
    # parity — the row is found by id regardless of status).
    again = client.delete(f"/api/v1/agent-instances/{instance_id}", headers=AUTH)
    assert again.status_code == 200


def test_delete_unknown_instance_404(client: TestClient) -> None:
    missing = client.delete(
        "/api/v1/agent-instances/66666666-6666-6666-6666-666666666666",
        headers=AUTH,
    )
    assert missing.status_code == 404


def test_delete_requires_bearer(client: TestClient) -> None:
    instance_id = _register(client)
    assert client.delete(f"/api/v1/agent-instances/{instance_id}").status_code == 401
    # The unauthorized call must not have deleted it.
    assert client.get("/api/v1/agent-instances", headers=AUTH).json()["total"] == 1


def test_sync_user_returns_success(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/sync-user",
        json={"id": "abc", "email": "a@b.c", "display_name": None},
        headers=AUTH,
    )
    assert response.status_code == 200
    assert response.json() == {"message": "User synced successfully"}


def test_sync_user_requires_bearer(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/sync-user",
        json={"id": "abc", "email": "a@b.c", "display_name": None},
    )
    assert response.status_code == 401


def test_billing_subscription_free_shape(client: TestClient) -> None:
    response = client.get("/api/v1/billing/subscription", headers=AUTH)
    assert response.status_code == 200
    assert response.json() == {
        "id": LOCAL_SUBSCRIPTION_ID,
        "plan_type": "free",
        "agent_limit": LOCAL_FREE_AGENT_LIMIT,
        "current_period_end": None,
        "cancel_at_period_end": False,
        "provider": None,
    }


def test_machines_empty_in_cloud_mode(store: LocalStore, tmp_path: Path) -> None:
    app = create_local_app(
        daemon=StubDaemon(),
        store=LocalStore(tmp_path / "cloud-store.db"),
        nonce=NONCE,
        allowed_origin=ORIGIN,
        local_only=False,
        terminal=TerminalService(),
    )
    with TestClient(app) as cloud_client:
        response = cloud_client.get("/api/v1/machines", headers=AUTH)
        assert response.json() == {"machines": []}
        health = cloud_client.get("/healthz").json()
        assert health["mode"] == "cloud" and health["machine_id"] is None


# ----------------------------------------------------------------------
# Heartbeat + queue lifecycle (direct-mode runner)
# ----------------------------------------------------------------------
def _user_message(store: LocalStore, instance_id: str, **kwargs) -> str:
    defaults: dict = {
        "agent_instance_id": instance_id,
        "content": "hello",
        "mark_as_read": False,
    }
    defaults.update(kwargs)
    return store.add_user_message(**defaults).id


def _message_by_id(store: LocalStore, instance_id: str, message_id: str):
    return next(
        msg
        for msg in store.get_instance_messages(instance_id, message_limit=100)
        if msg.id == message_id
    )


def _queue(message: Any) -> dict[str, Any]:
    assert message.message_metadata is not None
    return message.message_metadata["queue"]


def test_heartbeat_endpoint_updates_timestamp(
    client: TestClient, store: LocalStore
) -> None:
    instance_id = _register(client)
    before = store.get_instance(instance_id)
    assert before is not None

    response = client.post(
        f"/api/v1/agents/instances/{instance_id}/heartbeat", headers=AUTH
    )

    assert response.status_code == 200
    body = response.json()
    assert body["agent_instance_id"] == instance_id
    assert body["next_interval_seconds"] == 30
    assert body["last_heartbeat_at"].endswith("Z")
    after = store.get_instance(instance_id)
    assert after is not None
    assert after.last_heartbeat_at == body["last_heartbeat_at"]


def test_heartbeat_unknown_instance_404(client: TestClient) -> None:
    response = client.post(
        "/api/v1/agents/instances/missing-id/heartbeat", headers=AUTH
    )
    assert response.status_code == 404


def test_heartbeat_requires_bearer(client: TestClient) -> None:
    instance_id = _register(client)
    response = client.post(f"/api/v1/agents/instances/{instance_id}/heartbeat")
    assert response.status_code == 401


def test_consumed_endpoint_marks_queue(client: TestClient, store: LocalStore) -> None:
    instance_id = _register(client)
    message_id = _user_message(
        store, instance_id, message_metadata={"queue": {"status": "queued"}}
    )

    response = client.patch(f"/api/v1/messages/{message_id}/consumed", headers=AUTH)

    assert response.status_code == 200
    assert response.json() == {"success": True, "message_id": message_id}
    message = _message_by_id(store, instance_id, message_id)
    assert _queue(message)["status"] == "consumed"


def test_consumed_endpoint_records_steered(
    client: TestClient, store: LocalStore
) -> None:
    instance_id = _register(client)
    message_id = _user_message(store, instance_id)

    response = client.patch(
        f"/api/v1/messages/{message_id}/consumed",
        json={"steered": True},
        headers=AUTH,
    )

    assert response.status_code == 200
    message = _message_by_id(store, instance_id, message_id)
    assert _queue(message)["steered"] is True


def test_consumed_endpoint_does_not_overwrite_cancelled(
    client: TestClient, store: LocalStore
) -> None:
    instance_id = _register(client)
    message_id = _user_message(
        store, instance_id, message_metadata={"queue": {"status": "cancelled"}}
    )

    response = client.patch(f"/api/v1/messages/{message_id}/consumed", headers=AUTH)

    assert response.status_code == 200
    message = _message_by_id(store, instance_id, message_id)
    assert _queue(message) == {"status": "cancelled"}


def test_consumed_unknown_message_404(client: TestClient) -> None:
    response = client.patch("/api/v1/messages/missing-id/consumed", headers=AUTH)
    assert response.status_code == 404


def test_consumed_requires_bearer(client: TestClient, store: LocalStore) -> None:
    instance_id = _register(client)
    message_id = _user_message(store, instance_id)
    assert client.patch(f"/api/v1/messages/{message_id}/consumed").status_code == 401


def test_requeue_endpoint_only_touches_steer(
    client: TestClient, store: LocalStore
) -> None:
    instance_id = _register(client)
    steer_id = _user_message(
        store, instance_id, message_metadata={"queue": {"status": "steer"}}
    )
    queued_id = _user_message(
        store, instance_id, message_metadata={"queue": {"status": "queued"}}
    )

    response = client.patch(f"/api/v1/messages/{steer_id}/requeue", headers=AUTH)

    assert response.status_code == 200
    assert response.json() == {"success": True, "message_id": steer_id}
    steered = _message_by_id(store, instance_id, steer_id)
    assert _queue(steered)["status"] == "queued"
    untouched = _message_by_id(store, instance_id, queued_id)
    assert _queue(untouched) == {"status": "queued"}


def test_requeue_unknown_message_404(client: TestClient) -> None:
    response = client.patch("/api/v1/messages/missing-id/requeue", headers=AUTH)
    assert response.status_code == 404


def test_requeue_requires_bearer(client: TestClient, store: LocalStore) -> None:
    instance_id = _register(client)
    message_id = _user_message(store, instance_id)
    assert client.patch(f"/api/v1/messages/{message_id}/requeue").status_code == 401


def test_instance_message_is_queued_while_active(
    client: TestClient, store: LocalStore
) -> None:
    instance_id = _register(client)

    response = client.post(
        f"/api/v1/agent-instances/{instance_id}/messages",
        json={"content": "while busy"},
        headers=AUTH,
    )

    assert response.status_code == 200
    message = _message_by_id(store, instance_id, response.json()["id"])
    assert _queue(message)["status"] == "queued"


def test_cancel_queued_message_endpoint(client: TestClient, store: LocalStore) -> None:
    instance_id = _register(client)
    message_id = _user_message(store, instance_id)

    response = client.post(
        f"/api/v1/agent-instances/{instance_id}/messages/{message_id}/cancel",
        headers=AUTH,
    )

    assert response.status_code == 200
    assert response.json() == {"cancelled": True}
    assert _queue(_message_by_id(store, instance_id, message_id))["status"] == (
        "cancelled"
    )
    again = client.post(
        f"/api/v1/agent-instances/{instance_id}/messages/{message_id}/cancel",
        headers=AUTH,
    )
    assert again.json() == {"cancelled": False}


def test_steer_queued_message_endpoint(client: TestClient, store: LocalStore) -> None:
    instance_id = _register(client)
    message_id = _user_message(store, instance_id)

    response = client.post(
        f"/api/v1/agent-instances/{instance_id}/messages/{message_id}/steer",
        headers=AUTH,
    )

    assert response.status_code == 200
    assert response.json() == {"steered": True}
    assert _queue(_message_by_id(store, instance_id, message_id))["status"] == "steer"
    again = client.post(
        f"/api/v1/agent-instances/{instance_id}/messages/{message_id}/steer",
        headers=AUTH,
    )
    assert again.json() == {"steered": False}


def test_cancel_does_not_override_consumed_endpoint(
    client: TestClient, store: LocalStore
) -> None:
    instance_id = _register(client)
    message_id = _user_message(store, instance_id)
    client.patch(f"/api/v1/messages/{message_id}/consumed", headers=AUTH)

    response = client.post(
        f"/api/v1/agent-instances/{instance_id}/messages/{message_id}/cancel",
        headers=AUTH,
    )

    assert response.json() == {"cancelled": False}
    assert _queue(_message_by_id(store, instance_id, message_id))["status"] == (
        "consumed"
    )


def test_cancel_message_via_another_instance_404(
    client: TestClient, store: LocalStore
) -> None:
    owner_id = _register(client)
    other_id = _register(client)
    message_id = _user_message(store, owner_id)

    response = client.post(
        f"/api/v1/agent-instances/{other_id}/messages/{message_id}/cancel",
        headers=AUTH,
    )
    assert response.status_code == 404


def test_cancel_and_steer_unknown_message_404(client: TestClient) -> None:
    instance_id = _register(client)
    for action in ("cancel", "steer"):
        response = client.post(
            f"/api/v1/agent-instances/{instance_id}/messages/missing-id/{action}",
            headers=AUTH,
        )
        assert response.status_code == 404


def test_cancel_and_steer_require_bearer(client: TestClient, store: LocalStore) -> None:
    instance_id = _register(client)
    message_id = _user_message(store, instance_id)
    for action in ("cancel", "steer"):
        response = client.post(
            f"/api/v1/agent-instances/{instance_id}/messages/{message_id}/{action}"
        )
        assert response.status_code == 401


# ----------------------------------------------------------------------
# Public origin (Tailscale): CORS allow-list
# ----------------------------------------------------------------------
PUBLIC_ORIGIN = "https://machine.tailnet.ts.net"


@pytest.fixture
def public_origin_client(tmp_path: Path) -> Iterator[TestClient]:
    store = LocalStore(tmp_path / "cors-store.db")
    app = create_local_app(
        daemon=StubDaemon(),
        store=store,
        nonce=NONCE,
        allowed_origin=ORIGIN,
        local_only=True,
        terminal=TerminalService(),
        extra_allowed_origins=[PUBLIC_ORIGIN],
    )
    with TestClient(app) as test_client:
        yield test_client
    store.close()


def test_cors_allows_public_origin(public_origin_client: TestClient) -> None:
    preflight = public_origin_client.options(
        "/api/v1/machines",
        headers={
            "Origin": PUBLIC_ORIGIN,
            "Access-Control-Request-Method": "GET",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == PUBLIC_ORIGIN

    response = public_origin_client.get(
        "/api/v1/machines", headers={**AUTH, "Origin": PUBLIC_ORIGIN}
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == PUBLIC_ORIGIN


def test_cors_rejects_unknown_origin(public_origin_client: TestClient) -> None:
    preflight = public_origin_client.options(
        "/api/v1/machines",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert preflight.status_code == 400
    assert "access-control-allow-origin" not in preflight.headers

    response = public_origin_client.get(
        "/api/v1/machines",
        headers={**AUTH, "Origin": "https://evil.example"},
    )
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


# ----------------------------------------------------------------------
# Direct web dashboard: synthetic identity + empty cloud-data stubs
# ----------------------------------------------------------------------
def test_auth_me_returns_the_local_user(client: TestClient) -> None:
    response = client.get("/api/v1/auth/me", headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == LOCAL_USER_ID
    assert body["email"] == "local@vicoa.direct"
    assert body["display_name"] == "Local User"
    assert body["created_at"].endswith("Z")
    assert body["updated_at"].endswith("Z")


def test_auth_me_requires_bearer(client: TestClient) -> None:
    assert client.get("/api/v1/auth/me").status_code == 401


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/agents",
        "/api/v1/tasks",
        "/api/v1/projects",
        "/api/v1/automations",
    ],
)
def test_direct_data_lists_are_empty(client: TestClient, path: str) -> None:
    response = client.get(path, headers=AUTH)
    assert response.status_code == 200
    assert response.json() == []


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
HEIC_BYTES = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 16


def _upload_attachment(
    client: TestClient,
    instance_id: str,
    *,
    data: bytes = PNG_BYTES,
    filename: str = "pixel.png",
    mime_type: str = "image/png",
    headers: dict[str, str] = AUTH,
):
    return client.post(
        "/api/v1/attachments",
        files={"file": (filename, data, mime_type)},
        data={"agent_instance_id": instance_id},
        headers=headers,
    )


def test_attachment_upload_and_download(client: TestClient, store: LocalStore) -> None:
    instance_id = _register(client)

    response = _upload_attachment(client, instance_id)

    assert response.status_code == 200
    body = response.json()
    assert body["mime_type"] == "image/png"
    assert body["size_bytes"] == len(PNG_BYTES)
    assert body["filename"] == "pixel.png"
    attachment_id = body["id"]
    assert store.get_attachment(attachment_id) is not None

    with_bearer = client.get(f"/api/v1/attachments/{attachment_id}", headers=AUTH)
    assert with_bearer.status_code == 200
    assert with_bearer.content == PNG_BYTES
    assert with_bearer.headers["content-type"].startswith("image/png")

    with_query = client.get(f"/api/v1/attachments/{attachment_id}?token={NONCE}")
    assert with_query.status_code == 200
    assert with_query.content == PNG_BYTES


def test_attachment_download_requires_auth(client: TestClient) -> None:
    instance_id = _register(client)
    attachment_id = _upload_attachment(client, instance_id).json()["id"]

    assert client.get(f"/api/v1/attachments/{attachment_id}").status_code == 401
    assert (
        client.get(f"/api/v1/attachments/{attachment_id}?token=wrong").status_code
        == 401
    )
    assert (
        client.get("/api/v1/attachments/missing-attachment", headers=AUTH).status_code
        == 404
    )


def test_attachment_upload_requires_auth(client: TestClient) -> None:
    instance_id = _register(client)
    response = _upload_attachment(client, instance_id, headers={})
    assert response.status_code == 401


def test_attachment_upload_rejects_non_images(client: TestClient) -> None:
    instance_id = _register(client)

    text = _upload_attachment(
        client,
        instance_id,
        data=b"hello",
        filename="note.txt",
        mime_type="text/plain",
    )
    assert text.status_code == 400

    heic = _upload_attachment(
        client,
        instance_id,
        data=HEIC_BYTES,
        filename="photo.heic",
        mime_type="image/heic",
    )
    assert heic.status_code == 400


def test_attachment_upload_requires_an_instance(client: TestClient) -> None:
    response = _upload_attachment(client, "missing-instance")
    assert response.status_code == 404


def test_user_message_with_attachments_stamps_metadata(
    client: TestClient, store: LocalStore
) -> None:
    instance_id = _register(client)
    attachment_id = _upload_attachment(client, instance_id).json()["id"]

    response = client.post(
        f"/api/v1/agent-instances/{instance_id}/messages",
        json={"content": "", "attachment_ids": [attachment_id]},
        headers=AUTH,
    )

    assert response.status_code == 200
    message_id = response.json()["id"]
    detail = client.get(
        f"/api/v1/agent-instances/{instance_id}?message_limit=20",
        headers=AUTH,
    ).json()
    message = next(m for m in detail["messages"] if m["id"] == message_id)
    attachments = message["message_metadata"]["attachments"]
    assert attachments[0]["id"] == attachment_id
    bound = store.get_attachment(attachment_id)
    assert bound is not None
    assert bound.message_id == message_id


def test_user_message_rejects_unknown_attachment(
    client: TestClient,
) -> None:
    instance_id = _register(client)
    response = client.post(
        f"/api/v1/agent-instances/{instance_id}/messages",
        json={"content": "", "attachment_ids": ["missing-attachment"]},
        headers=AUTH,
    )
    assert response.status_code == 400


def test_end_session_terminates_the_runner(tmp_path: Path) -> None:
    class TerminatingDaemon(StubDaemon):
        def __init__(self) -> None:
            self.terminated: list[str] = []

        def terminate_session(self, agent_instance_id: str) -> bool:
            self.terminated.append(agent_instance_id)
            return True

    store = LocalStore(tmp_path / "end-session.db")
    daemon = TerminatingDaemon()
    app = create_local_app(
        daemon=daemon,
        store=store,
        nonce=NONCE,
        allowed_origin=ORIGIN,
        local_only=True,
        terminal=TerminalService(),
    )
    try:
        with TestClient(app) as client:
            instance_id = _register(client)
            response = client.post(
                "/api/v1/sessions/end",
                json={"agent_instance_id": instance_id},
                headers=AUTH,
            )
            assert response.status_code == 200
            assert response.json()["final_status"] == "COMPLETED"
        assert daemon.terminated == [instance_id]
    finally:
        store.close()


def test_activity_stub_shape(client: TestClient) -> None:
    response = client.get("/api/v1/activity", headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["daily"] == {}
    assert body["total_sessions"] == 0
    assert body["total_user_messages"] == 0
    assert body["total_messages"] == 0
    assert body["as_of"].endswith("Z")
