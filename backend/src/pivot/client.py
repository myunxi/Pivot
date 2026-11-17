"""Client for the Vicoa direct daemon.

Two transports share one credential (the direct-mode nonce):

* REST on ``127.0.0.1:8765`` with a ``Bearer`` header — instance lookups,
  renaming, messages, stop/delete.
* The local WebSocket ``/ws`` with subprotocols ``vicoa-ws`` (marker) and
  ``vicoa-key.<token>`` — the only client-facing way to launch a headless
  agent (``spawn-session``). The daemon only echoes a subprotocol when the
  marker is offered; ``websocket-client`` treats a missing echo as invalid.

``spawn-session`` blocks until the agent registers, so it uses a longer
timeout than REST. Every failure is normalised to ``PivotClientError``; the
engine decides whether a ticket becomes ``failed`` or stays ``deferred``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
from uuid import uuid4

import requests
import websocket

from .errors import PivotClientError

INSTANCE_PAGE_SIZE = 100
# The daemon echoes a subprotocol only when this marker is offered
# (``local_server.app._MARKER_SUBPROTOCOL``); without the echo
# ``websocket-client`` rejects the 101 response as an invalid header.
WS_MARKER_SUBPROTOCOL = "vicoa-ws"
# Daemon statuses that mean a session is still occupying a concurrency slot.
# Human sessions count too (C1): the limit is about machine capacity, not
# about who started the session.
ACTIVE_INSTANCE_STATUSES = frozenset({"STARTING", "ACTIVE", "AWAITING_INPUT", "PAUSED"})


@dataclass(frozen=True)
class SpawnResult:
    agent_instance_id: str
    worktree_path: str | None = None
    repo_root: str | None = None


def _default_ws_factory(url: str, *, subprotocols: list[str], timeout: float) -> Any:
    return websocket.create_connection(
        url,
        subprotocols=subprotocols,
        timeout=timeout,
        suppress_origin=True,
    )


class PivotClient:
    """Thin typed wrapper over the daemon's local REST + WS RPC surface."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 15.0,
        spawn_timeout: float = 120.0,
        http_session: Any | None = None,
        ws_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout
        self._spawn_timeout = spawn_timeout
        self._http = http_session if http_session is not None else requests.Session()
        self._http.trust_env = False  # never route loopback through a proxy
        self._ws_factory = ws_factory or _default_ws_factory

    # ------------------------------------------------------------------
    # REST
    # ------------------------------------------------------------------
    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        ok: tuple[int, ...] = (200,),
    ) -> Any:
        url = f"{self._base_url}{path}"
        try:
            response = self._http.request(
                method,
                url,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Accept": "application/json",
                },
                json=json_body,
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise PivotClientError(f"{method} {path} failed: {exc}") from exc
        if response.status_code not in ok:
            raise PivotClientError(
                self._error_message(method, path, response),
                status_code=response.status_code,
            )
        return response

    @staticmethod
    def _error_message(method: str, path: str, response: Any) -> str:
        detail: Any = None
        try:
            body = response.json()
        except Exception:  # noqa: BLE001 - body may not be JSON
            body = None
        if isinstance(body, dict):
            detail = body.get("detail") or body.get("error")
        if isinstance(detail, list):
            detail = "; ".join(str(item) for item in detail)
        if not detail:
            detail = getattr(response, "text", "") or "no response body"
        return f"{method} {path} -> {response.status_code}: {detail}"

    def list_instances(self) -> list[dict[str, Any]]:
        """Fetch every agent instance, page by page (direct stores are small)."""
        items: list[dict[str, Any]] = []
        offset = 0
        while True:
            response = self._request(
                "GET",
                f"/api/v1/agent-instances?limit={INSTANCE_PAGE_SIZE}&offset={offset}",
            )
            try:
                body = response.json()
            except Exception as exc:  # noqa: BLE001
                raise PivotClientError(f"invalid instance list: {exc}") from exc
            page = body.get("items") if isinstance(body, dict) else None
            if not isinstance(page, list):
                raise PivotClientError("instance list is missing 'items'")
            items.extend(item for item in page if isinstance(item, dict))
            if not page or not isinstance(body, dict) or not body.get("has_more"):
                return items
            offset += INSTANCE_PAGE_SIZE

    def active_session_count(self) -> int:
        return sum(
            1
            for item in self.list_instances()
            if item.get("status") in ACTIVE_INSTANCE_STATUSES
        )

    def get_instance(self, instance_id: str) -> dict[str, Any] | None:
        response = self._request(
            "GET", f"/api/v1/agent-instances/{instance_id}", ok=(200, 404)
        )
        if response.status_code == 404:
            return None
        body = response.json()
        if not isinstance(body, dict):
            raise PivotClientError("instance response is not an object")
        return body

    def rename_instance(self, instance_id: str, name: str) -> dict[str, Any]:
        response = self._request(
            "PATCH",
            f"/api/v1/agent-instances/{instance_id}",
            json_body={"name": name},
        )
        return response.json()

    def send_message(
        self,
        instance_id: str,
        content: str,
        *,
        mark_as_read: bool = False,
    ) -> dict[str, Any]:
        """Post a user message.

        ``mark_as_read=False`` is load-bearing for headless runs: advancing the
        instance cursor would make the wrapper's catch-up skip the prompt.
        """
        response = self._request(
            "POST",
            f"/api/v1/agent-instances/{instance_id}/messages",
            json_body={"content": content, "mark_as_read": mark_as_read},
            ok=(200, 201),
        )
        body = response.json()
        return body if isinstance(body, dict) else {}

    def stop_instance(self, instance_id: str) -> bool:
        """Mark the session COMPLETED; False when it was already finished."""
        response = self._request(
            "POST",
            "/api/v1/sessions/end",
            json_body={"agent_instance_id": instance_id},
            ok=(200, 400, 404),
        )
        return response.status_code == 200

    def delete_instance(self, instance_id: str) -> bool:
        response = self._request(
            "DELETE",
            f"/api/v1/agent-instances/{instance_id}",
            ok=(200, 204, 404),
        )
        return response.status_code != 404

    # ------------------------------------------------------------------
    # WebSocket RPC
    # ------------------------------------------------------------------
    def spawn(
        self,
        directory: str,
        agent: str,
        metadata: dict[str, Any] | None = None,
        worktree: dict[str, Any] | None = None,
    ) -> SpawnResult:
        params: dict[str, Any] = {
            "directory": directory,
            "agent": agent,
            "metadata": metadata or {},
        }
        if worktree is not None:
            params["worktree"] = worktree
        result = self._rpc("spawn-session", params)
        instance_id = result.get("agent_instance_id")
        if not isinstance(instance_id, str) or instance_id == "":
            raise PivotClientError("spawn-session returned no agent_instance_id")
        worktree_path = result.get("worktree_path")
        repo_root = result.get("repo_root")
        return SpawnResult(
            agent_instance_id=instance_id,
            worktree_path=worktree_path if isinstance(worktree_path, str) else None,
            repo_root=repo_root if isinstance(repo_root, str) else None,
        )

    def _ws_url(self) -> str:
        if self._base_url.startswith("https://"):
            base = "wss://" + self._base_url[len("https://") :]
        elif self._base_url.startswith("http://"):
            base = "ws://" + self._base_url[len("http://") :]
        else:
            raise PivotClientError(
                f"cannot derive a WebSocket URL from {self._base_url!r}"
            )
        return f"{base}/ws"

    def _rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            socket = self._ws_factory(
                self._ws_url(),
                subprotocols=[WS_MARKER_SUBPROTOCOL, f"vicoa-key.{self._token}"],
                timeout=self._spawn_timeout,
            )
        except Exception as exc:  # noqa: BLE001 - transport-specific
            raise PivotClientError(f"cannot connect to the daemon: {exc}") from exc

        request_id = uuid4().hex
        try:
            socket.send('{"type":"hello","scope":"user-scoped"}')
            socket.send(
                _json(
                    {
                        "type": "rpc-call",
                        "request_id": request_id,
                        "method": method,
                        "params": params,
                    }
                )
            )
            while True:
                try:
                    raw = socket.recv()
                except TimeoutError as exc:
                    raise PivotClientError(
                        f"{method} timed out after {self._spawn_timeout:g}s"
                    ) from exc
                except websocket.WebSocketTimeoutException as exc:
                    raise PivotClientError(
                        f"{method} timed out after {self._spawn_timeout:g}s"
                    ) from exc
                if raw == "" or raw is None:
                    raise PivotClientError(f"daemon closed the socket during {method}")
                try:
                    frame = _loads(raw)
                except ValueError as exc:
                    raise PivotClientError(
                        f"daemon sent invalid JSON during {method}: {exc}"
                    ) from exc
                frame_type = frame.get("type")
                if frame_type == "ping":
                    socket.send('{"type":"pong"}')
                    continue
                if frame.get("request_id") != request_id:
                    continue
                if frame_type == "rpc-error":
                    raise PivotClientError(
                        str(frame.get("error") or f"{method} failed")
                    )
                if frame_type != "rpc-result":
                    continue
                result = frame.get("result")
                if not isinstance(result, dict):
                    raise PivotClientError(f"{method} returned no result object")
                if result.get("error"):
                    raise PivotClientError(str(result["error"]))
                return result
        finally:
            try:
                socket.close()
            except Exception:  # noqa: BLE001 - cleanup must not mask the error
                pass


def _json(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False)


def _loads(raw: Any) -> dict[str, Any]:
    import json

    try:
        decoded = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(str(exc)) from exc
    if not isinstance(decoded, dict):
        raise ValueError("frame is not a JSON object")
    return decoded
