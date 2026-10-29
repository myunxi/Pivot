"""Same-origin web proxy: non-API HTTP goes to Next, local routes stay local."""

from __future__ import annotations

import json
import socket
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast

import pytest
from starlette.testclient import TestClient

from vicoa.local_server.app import create_local_app
from vicoa.local_server.store import LocalStore
from vicoa.terminal.service import TerminalService

NONCE = "0123456789abcdef0123456789abcdef"
ORIGIN = "http://localhost:3000"


class _UpstreamHandler(BaseHTTPRequestHandler):
    def _send(self, body: dict) -> None:
        payload = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        if self.path.startswith("/redirect"):
            self.send_response(307)
            self.send_header(
                "Location",
                "http://localhost:"
                f"{cast(ThreadingHTTPServer, self.server).server_port}"
                "/dashboard/sessions/new",
            )
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path.startswith("/external-redirect"):
            self.send_response(302)
            self.send_header("Location", "https://example.com/elsewhere")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._send(
            {
                "path": self.path,
                "host": self.headers.get("host"),
                "forwarded_host": self.headers.get("x-forwarded-host"),
                "forwarded_proto": self.headers.get("x-forwarded-proto"),
            }
        )

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", "0"))
        self._send({"echo": self.rfile.read(length).decode()})

    def log_message(self, *_args: object) -> None:
        pass


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def upstream() -> Iterator[str]:
    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), _UpstreamHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class _StubDaemon:
    machine_id: str | None = None

    def _handle_rpc_request(self, frame: dict) -> dict:
        return {}

    def _supported_rpc_methods(self) -> list[str]:
        return []

    def _detect_available_agents(self) -> dict[str, bool]:
        return {}

    def _capabilities(self) -> list[str]:
        return []


def _client(upstream_url: str, store: LocalStore) -> TestClient:
    app = create_local_app(
        daemon=_StubDaemon(),
        store=store,
        nonce=NONCE,
        allowed_origin=ORIGIN,
        local_only=True,
        terminal=TerminalService(),
        web_upstream=upstream_url,
    )
    return TestClient(app)


def test_proxies_unmatched_get_and_post(upstream: str, tmp_path: Path) -> None:
    store = LocalStore(tmp_path / "proxy.db")
    try:
        with _client(upstream, store) as client:
            response = client.get("/dashboard/sessions/new?x=1")
            assert response.status_code == 200
            assert response.json()["path"] == "/dashboard/sessions/new?x=1"
            assert response.json()["forwarded_proto"] == "http"

            echo = client.post("/api/attachments", content=b"hello")
            assert echo.status_code == 200
            assert echo.json() == {"echo": "hello"}
    finally:
        store.close()


def test_rewrites_loopback_redirects_to_the_proxy_origin(
    upstream: str, tmp_path: Path
) -> None:
    store = LocalStore(tmp_path / "proxy-redirect.db")
    try:
        with _client(upstream, store) as client:
            response = client.get("/redirect", follow_redirects=False)
            assert response.status_code == 307
            assert (
                response.headers["location"]
                == "http://testserver/dashboard/sessions/new"
            )

            external = client.get("/external-redirect", follow_redirects=False)
            assert external.headers["location"] == "https://example.com/elsewhere"
    finally:
        store.close()


def test_local_routes_still_win(upstream: str, tmp_path: Path) -> None:
    store = LocalStore(tmp_path / "proxy-local.db")
    try:
        with _client(upstream, store) as client:
            health = client.get("/healthz")
            assert health.json()["mode"] == "local-only"
            machines = client.get(
                "/api/v1/machines",
                headers={"Authorization": f"Bearer {NONCE}"},
            )
            assert machines.status_code == 200
            assert machines.json()["machines"][0]["machine_id"] == "local"
    finally:
        store.close()


def test_upstream_down_returns_502(tmp_path: Path) -> None:
    store = LocalStore(tmp_path / "proxy-down.db")
    try:
        with _client(f"http://127.0.0.1:{_free_port()}", store) as client:
            response = client.get("/dashboard")
            assert response.status_code == 502
    finally:
        store.close()
