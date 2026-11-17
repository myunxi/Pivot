"""Pivot daemon client: REST calls and the spawn-session WebSocket RPC."""

import json
from typing import Any, Callable

import pytest

from pivot.client import PivotClient, SpawnResult
from pivot.errors import PivotClientError


class FakeResponse:
    def __init__(self, status_code: int = 200, body: Any = None):
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body) if body is not None else ""

    def json(self) -> Any:
        return self._body


class FakeSession:
    def __init__(self, handler: Callable[[str, str, dict[str, Any]], Any]):
        self.handler = handler
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.trust_env = True

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((method, url, kwargs))
        result = self.handler(method, url, kwargs)
        if isinstance(result, Exception):
            raise result
        return result


class FakeSocket:
    """Answers an rpc-call on send; also emits a ping to exercise pong."""

    def __init__(
        self,
        result: dict[str, Any],
        *,
        close_on_recv: bool = False,
        respond: bool = True,
    ):
        self.result = result
        self.close_on_recv = close_on_recv
        self.respond = respond
        self.sent: list[dict[str, Any]] = []
        self.closed = False
        self.timeout: float | None = None
        self._pending: list[dict[str, Any]] = []

    def send(self, payload: str) -> None:
        frame = json.loads(payload)
        self.sent.append(frame)
        if frame.get("type") == "rpc-call" and self.respond:
            self._pending.append({"type": "ping"})
            self._pending.append(
                {
                    "type": "rpc-result",
                    "request_id": frame.get("request_id"),
                    "result": self.result,
                }
            )

    def recv(self) -> str:
        if self.close_on_recv:
            return ""
        if not self._pending:
            raise TimeoutError("no frame")
        return json.dumps(self._pending.pop(0))

    def settimeout(self, value: float) -> None:
        self.timeout = value

    def close(self) -> None:
        self.closed = True


def make_client(
    *,
    session: FakeSession | None = None,
    socket: FakeSocket | None = None,
    base_url: str = "http://127.0.0.1:8765/",
) -> tuple[PivotClient, list[dict[str, Any]]]:
    ws_calls: list[dict[str, Any]] = []

    def ws_factory(url: str, *, subprotocols: list[str], timeout: float) -> FakeSocket:
        ws_calls.append({"url": url, "subprotocols": subprotocols, "timeout": timeout})
        assert socket is not None
        return socket

    client = PivotClient(
        base_url,
        "secret-token",
        http_session=session or FakeSession(lambda *_: FakeResponse(200, {})),
        ws_factory=ws_factory,
    )
    return client, ws_calls


def test_spawn_sends_hello_and_rpc_frames() -> None:
    socket = FakeSocket(
        {"agent_instance_id": "inst-1", "worktree_path": "/wt", "repo_root": "/repo"}
    )
    client, ws_calls = make_client(socket=socket)

    result = client.spawn(
        "/tmp/project",
        "claude",
        metadata={"model": "claude-sonnet-5-5"},
        worktree={"new": True},
    )

    assert result == SpawnResult("inst-1", "/wt", "/repo")
    assert ws_calls == [
        {
            "url": "ws://127.0.0.1:8765/ws",
            # The daemon only echoes a subprotocol when the marker is offered,
            # and websocket-client treats a missing echo as an invalid header.
            "subprotocols": ["vicoa-ws", "vicoa-key.secret-token"],
            "timeout": 120.0,
        }
    ]
    assert socket.sent[0] == {"type": "hello", "scope": "user-scoped"}
    rpc = socket.sent[1]
    assert rpc["type"] == "rpc-call"
    assert rpc["method"] == "spawn-session"
    assert rpc["params"] == {
        "directory": "/tmp/project",
        "agent": "claude",
        "metadata": {"model": "claude-sonnet-5-5"},
        "worktree": {"new": True},
    }
    assert any(frame.get("type") == "pong" for frame in socket.sent)
    assert socket.closed is True


def test_spawn_surfaces_daemon_error() -> None:
    socket = FakeSocket({"error": "agent is not installed"})
    client, _ = make_client(socket=socket)

    with pytest.raises(PivotClientError, match="agent is not installed"):
        client.spawn("/tmp/project", "claude")


def test_spawn_rejects_missing_instance_id() -> None:
    client, _ = make_client(socket=FakeSocket({"repo_root": "/repo"}))

    with pytest.raises(PivotClientError, match="agent_instance_id"):
        client.spawn("/tmp/project", "claude")


def test_spawn_closed_socket() -> None:
    client, _ = make_client(socket=FakeSocket({}, close_on_recv=True))

    with pytest.raises(PivotClientError, match="closed"):
        client.spawn("/tmp/project", "claude")


def test_spawn_times_out() -> None:
    client, _ = make_client(socket=FakeSocket({}, respond=False))

    with pytest.raises(PivotClientError, match="timed out"):
        client.spawn("/tmp/project", "claude")


def test_rest_calls_use_bearer_and_strip_trailing_slash() -> None:
    def handler(method: str, url: str, kwargs: dict[str, Any]) -> FakeResponse:
        if url.endswith("/api/v1/agent-instances/inst-1"):
            return FakeResponse(200, {"agent_instance_id": "inst-1", "name": "n"})
        return FakeResponse(200, {"agent_instance_id": "inst-1"})

    session = FakeSession(handler)
    client, _ = make_client(session=session)

    client.rename_instance("inst-1", "New title")
    client.send_message("inst-1", "hello")

    method, url, kwargs = session.calls[0]
    assert method == "PATCH"
    assert url == "http://127.0.0.1:8765/api/v1/agent-instances/inst-1"
    assert kwargs["headers"]["Authorization"] == "Bearer secret-token"
    assert kwargs["json"] == {"name": "New title"}
    assert session.calls[1][2]["json"] == {
        "content": "hello",
        "mark_as_read": False,
    }
    assert session.trust_env is False


def test_stop_and_delete_are_idempotent() -> None:
    statuses = {"stop": 400, "delete": 404}

    def handler(method: str, url: str, kwargs: dict[str, Any]) -> FakeResponse:
        if url.endswith("/sessions/end"):
            return FakeResponse(statuses["stop"], {})
        return FakeResponse(statuses["delete"], {})

    client, _ = make_client(session=FakeSession(handler))

    assert client.stop_instance("inst-1") is False
    assert client.delete_instance("inst-1") is False

    statuses["stop"] = 200
    statuses["delete"] = 200
    assert client.stop_instance("inst-1") is True
    assert client.delete_instance("inst-1") is True


def test_get_instance_returns_none_on_404() -> None:
    client, _ = make_client(
        session=FakeSession(lambda *_: FakeResponse(404, {"detail": "missing"}))
    )

    assert client.get_instance("inst-1") is None


def test_http_error_carries_status() -> None:
    client, _ = make_client(
        session=FakeSession(lambda *_: FakeResponse(500, {"detail": "boom"}))
    )

    with pytest.raises(PivotClientError) as caught:
        client.rename_instance("inst-1", "x")

    assert caught.value.status_code == 500
    assert "boom" in str(caught.value)


def test_active_session_count_paginates_and_ignores_terminal_rows() -> None:
    def handler(method: str, url: str, kwargs: dict[str, Any]) -> FakeResponse:
        if "offset=0" in url:
            return FakeResponse(
                200,
                {
                    "items": [
                        {"id": "1", "status": "ACTIVE"},
                        {"id": "2", "status": "AWAITING_INPUT"},
                    ],
                    "has_more": True,
                },
            )
        return FakeResponse(
            200,
            {
                "items": [
                    {"id": "3", "status": "COMPLETED"},
                    {"id": "4", "status": "PAUSED"},
                ],
                "has_more": False,
            },
        )

    client, _ = make_client(session=FakeSession(handler))

    assert client.active_session_count() == 3
