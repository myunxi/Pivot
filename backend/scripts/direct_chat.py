#!/usr/bin/env python3
"""Minimal client for a Vicoa direct-mode daemon.

Spawns one headless session over the local WebSocket RPC, sends the prompt as a
user message over REST, and prints the agent output plus the queue lifecycle
until the turn ends (the instance returns to AWAITING_INPUT) or the idle
timeout expires.

Example::

    TOKEN=$(cat ~/.vicoa/direct/token)
    python scripts/direct_chat.py --nonce "$TOKEN" \
        --cwd ~/project --prompt "Reply with exactly: pong"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any
from urllib.parse import urlparse, urlunparse

import requests
import websocket

DEFAULT_WS_URL = "ws://127.0.0.1:8765/ws"
DEFAULT_IDLE_SECONDS = 30.0
SPAWN_TIMEOUT_SECONDS = 60.0


def http_base_from_ws(ws_url: str) -> str:
    """Map ``ws(s)://host/path`` to the matching REST origin."""
    parsed = urlparse(ws_url)
    scheme = {"ws": "http", "wss": "https"}.get(parsed.scheme)
    if scheme is None or not parsed.netloc:
        raise ValueError(f"invalid WebSocket URL: {ws_url!r}")
    return urlunparse((scheme, parsed.netloc, "", "", "", ""))


def queue_status(metadata: dict[str, Any] | None) -> str | None:
    """Read ``message_metadata.queue.status`` defensively."""
    if not isinstance(metadata, dict):
        return None
    queue = metadata.get("queue")
    if not isinstance(queue, dict):
        return None
    status = queue.get("status")
    return status if isinstance(status, str) else None


class DirectChat:
    def __init__(
        self,
        *,
        ws_url: str,
        http_url: str,
        token: str,
        cwd: str,
        agent: str,
        prompt: str | None,
        model: str | None,
        permission_mode: str | None,
        exit_after_idle: float,
        verbose: bool,
    ) -> None:
        self.ws_url = ws_url
        self.http_url = http_url.rstrip("/")
        self.token = token
        self.cwd = cwd
        self.agent = agent
        self.prompt = prompt
        self.model = model
        self.permission_mode = permission_mode
        self.exit_after_idle = exit_after_idle
        self.verbose = verbose

        self.ws: websocket.WebSocket | None = None
        self.instance_id: str | None = None
        self.user_message_id: str | None = None
        self.saw_agent = False
        self.session_ended = False
        self._request_seq = 0

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------
    def _connect(self) -> None:
        self.ws = websocket.create_connection(
            self.ws_url,
            subprotocols=["vicoa-ws", f"vicoa-local.{self.token}"],
            timeout=10.0,
        )
        self._send({"type": "hello", "scope": "user-scoped"})
        frame = self._receive(10.0)
        if frame is None or frame.get("type") != "server_info":
            raise RuntimeError("direct WS did not return server_info")

    def _send(self, frame: dict[str, Any]) -> None:
        assert self.ws is not None
        self.ws.send(json.dumps(frame))

    def _receive(self, timeout: float) -> dict[str, Any] | None:
        assert self.ws is not None
        self.ws.settimeout(max(0.1, timeout))
        try:
            raw = self.ws.recv()
        except websocket.WebSocketTimeoutException:
            return None
        if not raw:
            return None
        try:
            frame = json.loads(raw)
        except ValueError:
            if self.verbose:
                print(f"[debug] non-JSON frame: {raw!r}", flush=True)
            return None
        return frame if isinstance(frame, dict) else None

    def _rpc(self, method: str, params: dict[str, Any], *, timeout: float) -> dict:
        assert self.ws is not None
        self._request_seq += 1
        request_id = f"direct-chat-{self._request_seq}"
        self._send(
            {
                "type": "rpc-call",
                "request_id": request_id,
                "machine_id": "local",
                "method": method,
                "params": params,
            }
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            frame = self._receive(min(1.0, deadline - time.monotonic()))
            if frame is None:
                continue
            if (
                frame.get("type") == "rpc-result"
                and frame.get("request_id") == request_id
            ):
                result = frame.get("result")
                return result if isinstance(result, dict) else {}
            self._handle_frame(frame)
        raise TimeoutError(f"RPC {method} timed out after {timeout:.0f}s")

    # ------------------------------------------------------------------
    # REST
    # ------------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def _post_user_message(self, content: str) -> str:
        assert self.instance_id is not None
        response = requests.post(
            f"{self.http_url}/api/v1/agent-instances/{self.instance_id}/messages",
            headers=self._headers(),
            json={"content": content},
            timeout=15.0,
        )
        response.raise_for_status()
        return str(response.json()["id"])

    def _end_session(self) -> None:
        if self.instance_id is None or self.session_ended:
            return
        try:
            requests.post(
                f"{self.http_url}/api/v1/sessions/end",
                headers=self._headers(),
                json={"agent_instance_id": self.instance_id},
                timeout=10.0,
            )
        except requests.RequestException as exc:
            print(f"[warn] failed to end session: {exc}", flush=True)
        self.session_ended = True
        print(f"[system] session {self.instance_id} ended", flush=True)

    # ------------------------------------------------------------------
    # Frames
    # ------------------------------------------------------------------
    def _handle_frame(self, frame: dict[str, Any]) -> bool:
        kind = frame.get("type")
        if kind == "ping":
            self._send({"type": "pong"})
            return False
        if kind != "update":
            if self.verbose:
                print(f"[debug] frame {kind}", flush=True)
            return False

        payload = frame.get("payload") or {}
        entity = payload.get("entity")
        body = payload.get("body") or {}
        body_type = body.get("t")

        if entity == "agent_instances":
            instance_id = str(body.get("id") or "")
            if body_type == "instance-created" and instance_id:
                print(f"[system] session {instance_id} spawned", flush=True)
            elif body_type == "instance-update" and instance_id == self.instance_id:
                if body.get("status") == "AWAITING_INPUT" and self.saw_agent:
                    # The turn is over; let the caller's finally block POST
                    # /sessions/end (which also stops the runner).
                    return True
            return False

        if entity != "messages":
            return False
        message_id = str(body.get("id") or "")
        if body_type == "new-message":
            if body.get("sender_type") == "AGENT":
                self.saw_agent = True
                print(f"[agent] {body.get('content', '')}", flush=True)
            return False
        if body_type == "message-update":
            status = queue_status(body.get("message_metadata"))
            if message_id == self.user_message_id and status is not None:
                print(f"[queue] {message_id} -> {status}", flush=True)
            return False
        return False

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------
    def run(self) -> int:
        self._connect()
        metadata: dict[str, Any] = {}
        if self.model:
            metadata["model"] = self.model
        if self.permission_mode:
            metadata["permission_mode"] = self.permission_mode
        try:
            result = self._rpc(
                "spawn-session",
                {
                    "directory": self.cwd,
                    "agent": self.agent,
                    "metadata": metadata,
                },
                timeout=SPAWN_TIMEOUT_SECONDS,
            )
            if result.get("error"):
                print(f"[error] spawn failed: {result['error']}", flush=True)
                return 1
            instance_id = result.get("agent_instance_id")
            if not isinstance(instance_id, str) or not instance_id:
                print(
                    f"[error] spawn returned no agent_instance_id: {result}", flush=True
                )
                return 1
            self.instance_id = instance_id

            if self.prompt:
                self.user_message_id = self._post_user_message(self.prompt)
                print(f"[you] {self.prompt}", flush=True)

            deadline: float | None = (
                None
                if self.exit_after_idle <= 0
                else time.monotonic() + self.exit_after_idle
            )

            while True:
                if deadline is None:
                    wait = 1.0
                else:
                    wait = max(0.1, min(1.0, deadline - time.monotonic()))
                frame = self._receive(wait)
                if frame is None:
                    if deadline is not None and time.monotonic() >= deadline:
                        print("[system] idle timeout; ending session", flush=True)
                        break
                    continue
                if deadline is not None:
                    deadline = time.monotonic() + self.exit_after_idle
                if self._handle_frame(frame):
                    break
        except (requests.RequestException, RuntimeError, TimeoutError) as exc:
            print(f"[error] {exc}", flush=True)
            return 1
        finally:
            self._end_session()
            if self.ws is not None:
                self.ws.close()
        return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Talk to a Vicoa direct-mode daemon without the web UI."
    )
    parser.add_argument("--ws-url", default=DEFAULT_WS_URL)
    parser.add_argument(
        "--http-url",
        default=None,
        help="REST origin (default: derived from --ws-url)",
    )
    parser.add_argument(
        "--nonce",
        required=True,
        help="Direct-mode token (also accepted as --token)",
    )
    parser.add_argument("--cwd", default=os.getcwd(), help="Session working directory")
    parser.add_argument("--agent", default="claude")
    parser.add_argument("--prompt", default=None, help="Initial user message")
    parser.add_argument("--permission-mode", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument(
        "--exit-after-idle",
        type=float,
        default=DEFAULT_IDLE_SECONDS,
        help="Seconds without frames before ending the session (0 = wait forever)",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    http_url = args.http_url or http_base_from_ws(args.ws_url)
    chat = DirectChat(
        ws_url=args.ws_url,
        http_url=http_url,
        token=args.nonce,
        cwd=os.path.abspath(os.path.expanduser(args.cwd)),
        agent=args.agent,
        prompt=args.prompt,
        model=args.model,
        permission_mode=args.permission_mode,
        exit_after_idle=args.exit_after_idle,
        verbose=args.verbose,
    )
    return chat.run()


if __name__ == "__main__":
    sys.exit(main())
