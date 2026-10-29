"""Local-only daemon wiring: the headless child must target the local server.

``run_local_only_daemon`` constructs a ``MachineDaemon`` with
``api_key=<nonce>`` and ``base_url=http://127.0.0.1:<port>`` and never calls
``run()``. ``spawn_session_rpc`` then builds the headless command from those
same fields — so the child's SDK talks to the local server with the nonce as
its API key. These tests pin that handoff mechanism.

Also here: the /healthz ``cloud`` field — the shared ``CloudAuthStatus``
object the runner hands to both the daemon thread (writer, via
``on_cloud_status``) and the local server thread (reader) so the desktop
tray can distinguish starting / connected / auth_failed.
"""

import os
import socket
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import cast

import pytest
import requests
from starlette.testclient import TestClient

from vicoa.local_server.app import CloudAuthStatus, create_local_app
from vicoa.local_server.runner import (
    DEFAULT_DIRECT_STORE_PATH,
    DEFAULT_DIRECT_TOKEN_PATH,
    LocalListenerLifecycle,
    LocalListenerSettings,
    _local_uvicorn_server,
    _normalize_public_origin,
    build_direct_runtime,
    load_or_create_direct_token,
    local_base_url,
)
from vicoa.local_server.store import LocalStore
from vicoa.machine_daemon import MachineDaemon
from vicoa.sdk.exceptions import AuthenticationError
from vicoa.terminal.service import TerminalService
from vicoa.utils import derive_ws_url

NONCE = "0011223344556677889900112233445566778899"
ORIGIN = "http://localhost:3000"


def test_local_base_url_is_loopback() -> None:
    assert local_base_url(45991) == "http://127.0.0.1:45991"


def test_headless_command_targets_local_server() -> None:
    daemon = MachineDaemon(api_key=NONCE, base_url=local_base_url(45991))
    command = daemon._build_headless_command(
        directory="/tmp/proj",
        agent="claude",
        session_id="abc-123",
    )
    # The SDK base-url handoff: explicit --api-key/--base-url argv (env
    # VICOA_API_KEY is only a setdefault fallback in spawn_session_rpc).
    api_key_index = command.index("--api-key")
    assert command[api_key_index + 1] == NONCE
    base_url_index = command.index("--base-url")
    assert command[base_url_index + 1] == "http://127.0.0.1:45991"


def test_child_ws_url_derives_from_local_base_url() -> None:
    # The headless child's session WS client uses VICOA_WS_URL or derives
    # from --base-url; local-only mode clears the env override so this
    # derivation is what connects it to the local /ws endpoint.
    assert derive_ws_url(local_base_url(45991)) == "ws://127.0.0.1:45991/ws"


def test_listener_settings_shape() -> None:
    settings = LocalListenerSettings(
        port=45991,
        nonce=NONCE,
        allowed_origin="http://localhost:3000",
        local_only=True,
    )
    assert settings.local_only is True
    assert settings.allowed_origin == "http://localhost:3000"


# ----------------------------------------------------------------------
# Direct mode: no cloud account, local daemon + file-backed SQLite.
# ----------------------------------------------------------------------
def test_direct_default_store_path_is_under_vicoa_home() -> None:
    assert DEFAULT_DIRECT_STORE_PATH == Path.home() / ".vicoa" / "direct" / "store.db"


def test_build_direct_runtime_targets_loopback_and_file_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_path = tmp_path / "direct" / "store.db"
    monkeypatch.setenv("VICOA_WS_URL", "ws://cloud.example/ws")

    runtime = build_direct_runtime(
        port=45991,
        store_path=store_path,
        nonce=NONCE,
        allowed_origin=ORIGIN,
    )
    try:
        assert runtime.settings.local_only is True
        assert runtime.settings.nonce == NONCE
        assert runtime.settings.allowed_origin == ORIGIN
        assert runtime.daemon.base_url == "http://127.0.0.1:45991"
        assert runtime.daemon.api_key == NONCE
        assert runtime.store_path == store_path
        assert store_path.exists()
        # A leftover cloud override would point spawned runners at the cloud.
        assert "VICOA_WS_URL" not in os.environ
    finally:
        runtime.store.close()


@pytest.mark.parametrize("port", [0, -1, 65536, 70000])
def test_build_direct_runtime_rejects_invalid_port(port: int, tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        build_direct_runtime(port=port, store_path=tmp_path / "store.db")


def test_build_direct_runtime_normalizes_public_origin(tmp_path: Path) -> None:
    runtime = build_direct_runtime(
        port=45991,
        store_path=tmp_path / "store.db",
        nonce=NONCE,
        public_url="https://machine.tailnet.ts.net/some/path",
    )
    try:
        assert runtime.settings.public_origin == "https://machine.tailnet.ts.net"
    finally:
        runtime.store.close()


# ----------------------------------------------------------------------
# Persistent token + public origin
# ----------------------------------------------------------------------
def test_direct_default_token_path_is_under_vicoa_home() -> None:
    assert DEFAULT_DIRECT_TOKEN_PATH == Path.home() / ".vicoa" / "direct" / "token"


def test_load_or_create_token_generates_persists_and_reuses(
    tmp_path: Path,
) -> None:
    token_path = tmp_path / "direct" / "token"
    token, created, resolved = load_or_create_direct_token(token_path=token_path)

    assert created is True
    assert resolved == token_path
    assert len(token) == 64
    assert token_path.read_text(encoding="utf-8").strip() == token
    assert token_path.stat().st_mode & 0o777 == 0o600

    again, created_again, _ = load_or_create_direct_token(token_path=token_path)
    assert again == token
    assert created_again is False


def test_load_or_create_token_rotates(tmp_path: Path) -> None:
    token_path = tmp_path / "token"
    first, _, _ = load_or_create_direct_token(token_path=token_path)

    rotated, created, _ = load_or_create_direct_token(
        token_path=token_path, rotate=True
    )

    assert created is True
    assert rotated != first
    assert token_path.read_text(encoding="utf-8").strip() == rotated


def test_explicit_token_does_not_touch_the_token_file(tmp_path: Path) -> None:
    token_path = tmp_path / "token"

    token, created, resolved = load_or_create_direct_token(
        token_path=token_path, explicit="explicit-token"
    )

    assert token == "explicit-token"
    assert created is False
    assert resolved == token_path
    assert not token_path.exists()


def test_load_or_create_token_replaces_an_empty_file(tmp_path: Path) -> None:
    token_path = tmp_path / "token"
    token_path.write_text("", encoding="utf-8")

    token, created, _ = load_or_create_direct_token(token_path=token_path)

    assert created is True
    assert token.strip() != ""
    assert token_path.read_text(encoding="utf-8").strip() == token


# ----------------------------------------------------------------------
# Direct-mode session process cleanup
# ----------------------------------------------------------------------
class _FakeProcess:
    def __init__(self, *, pid: int = 999999, stubborn: bool = False) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False
        self._stubborn = stubborn

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        if not self._stubborn:
            self.returncode = -15

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            raise subprocess.TimeoutExpired("fake", timeout or 0)
        return self.returncode


def _as_popen(process: _FakeProcess) -> subprocess.Popen[bytes]:
    return cast("subprocess.Popen[bytes]", process)


def _daemon() -> MachineDaemon:
    return MachineDaemon(api_key="test-key", base_url="http://localhost:0")


def test_terminate_session_stops_registered_process() -> None:
    daemon = _daemon()
    process = _FakeProcess()
    daemon._register_session_process("session-a", _as_popen(process))

    assert daemon.terminate_session("session-a") is True

    assert process.terminated is True
    assert daemon._consume_intentionally_terminated("session-a") is True
    assert daemon.terminate_session("session-a") is False


def test_terminate_session_escalates_to_kill() -> None:
    daemon = _daemon()
    process = _FakeProcess(stubborn=True)
    daemon._register_session_process("session-b", _as_popen(process))

    daemon.terminate_session("session-b", timeout=0.01)

    assert process.terminated is True
    assert process.killed is True
    assert process.returncode == -9


def test_terminate_all_sessions_stops_every_runner() -> None:
    daemon = _daemon()
    first = _FakeProcess()
    second = _FakeProcess()
    daemon._register_session_process("session-a", _as_popen(first))
    daemon._register_session_process("session-b", _as_popen(second))

    stopped = daemon.terminate_all_sessions(timeout=0.01)

    assert sorted(stopped) == ["session-a", "session-b"]
    assert first.terminated and second.terminated
    assert daemon._consume_intentionally_terminated("session-a") is True
    assert daemon._consume_intentionally_terminated("session-b") is True


def test_monitor_skips_status_fallback_for_intentional_termination() -> None:
    daemon = _daemon()
    process = _FakeProcess()
    process.returncode = -15
    updates: list[tuple[str, str]] = []
    daemon._update_instance_status = lambda sid, status: updates.append((sid, status))  # type: ignore[method-assign]
    daemon._register_session_process("session-c", _as_popen(process))
    daemon._mark_intentionally_terminated("session-c")

    daemon._monitor_session_process(
        request_id="", session_id="session-c", process=_as_popen(process)
    )

    assert updates == []
    assert daemon._session_processes == {}


def test_normalize_public_origin() -> None:
    assert _normalize_public_origin("https://machine.tailnet.ts.net/") == (
        "https://machine.tailnet.ts.net"
    )
    assert _normalize_public_origin("http://host:8080/path") == ("http://host:8080")
    for invalid in ("", "not-a-url", "ftp://host", "https://"):
        with pytest.raises(ValueError):
            _normalize_public_origin(invalid)


# ----------------------------------------------------------------------
# /healthz cloud field: null in local-only, connecting/connected/auth_failed
# in cloud mode (the Electron tray parses this).
# ----------------------------------------------------------------------
class _StubDaemon:
    """The slice of MachineDaemon the local app consumes."""

    machine_id: str | None = "mac-42"

    def _handle_rpc_request(self, frame: dict) -> dict:
        return {}

    def _supported_rpc_methods(self) -> list[str]:
        return []

    def _detect_available_agents(self) -> dict[str, bool]:
        return {}

    def _capabilities(self) -> list[str]:
        return []


@pytest.fixture
def store(tmp_path: Path) -> Iterator[LocalStore]:
    s = LocalStore(tmp_path / "store.db")
    yield s
    s.close()


def _make_client(
    store: LocalStore,
    *,
    local_only: bool,
    cloud_status: CloudAuthStatus | None = None,
) -> TestClient:
    app = create_local_app(
        daemon=_StubDaemon(),
        store=store,
        nonce=NONCE,
        allowed_origin=ORIGIN,
        local_only=local_only,
        terminal=TerminalService(),
        cloud_status=cloud_status,
    )
    return TestClient(app)


def test_healthz_local_only_reports_cloud_null(store: LocalStore) -> None:
    with _make_client(store, local_only=True) as client:
        body = client.get("/healthz").json()
    assert body == {
        "status": "ok",
        "mode": "local-only",
        "machine_id": "local",
        "cloud": None,
    }


def test_healthz_cloud_mode_transitions(store: LocalStore) -> None:
    """The daemon thread flips the shared status object; /healthz follows.

    Existing fields (status/mode/machine_id) keep their exact shape — the
    Electron shell already parses them.
    """
    status = CloudAuthStatus()
    with _make_client(store, local_only=False, cloud_status=status) as client:
        body = client.get("/healthz").json()
        assert body == {
            "status": "ok",
            "mode": "cloud",
            "machine_id": "mac-42",
            "cloud": "connecting",
        }

        status.set(CloudAuthStatus.CONNECTED)
        assert client.get("/healthz").json()["cloud"] == "connected"

        status.set(CloudAuthStatus.AUTH_FAILED)
        assert client.get("/healthz").json()["cloud"] == "auth_failed"


# ----------------------------------------------------------------------
# Daemon-side transitions: register success -> connected, fatal auth ->
# auth_failed. These are the two write points the runner wires up via
# ``daemon.on_cloud_status = cloud_status.set``.
# ----------------------------------------------------------------------
class _FakeResp:
    def __init__(self, status_code: int, json_body: dict | None = None) -> None:
        self.status_code = status_code
        self._json = json_body or {}
        self.text = ""

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def json(self) -> dict:
        return self._json


def test_register_machine_success_marks_connected(monkeypatch) -> None:
    daemon = MachineDaemon(api_key="test-key", base_url="http://localhost:0")
    status = CloudAuthStatus()
    daemon.on_cloud_status = status.set

    monkeypatch.setattr(
        "vicoa.machine_daemon.migrate_legacy_flat_state", lambda *_a, **_k: None
    )
    monkeypatch.setattr(daemon, "_load_entry", lambda: {})
    monkeypatch.setattr(daemon, "_persist_entry", lambda updates: None)
    monkeypatch.setattr(daemon, "_detect_available_agents", lambda: {})
    monkeypatch.setattr(
        daemon.session,
        "post",
        lambda *a, **k: _FakeResp(
            200,
            {
                "machine_id": "mac-1",
                "display_name": None,
                "hostname": "h",
                "platform": "p",
            },
        ),
    )

    assert status.value == "connecting"
    daemon.register_machine()
    assert status.value == "connected"


def test_handle_fatal_auth_marks_auth_failed(monkeypatch) -> None:
    daemon = MachineDaemon(api_key="test-key", base_url="http://localhost:0")
    status = CloudAuthStatus()
    daemon.on_cloud_status = status.set
    monkeypatch.setattr(daemon, "_persist_entry", lambda updates: None)

    daemon._handle_fatal_auth()

    assert status.value == "auth_failed"


def test_cloud_status_callback_errors_never_crash_the_daemon(monkeypatch) -> None:
    """The callback only feeds the tray; a broken one must not take the
    fatal-auth path (or registration) down with it."""
    daemon = MachineDaemon(api_key="test-key", base_url="http://localhost:0")

    def _boom(_status: str) -> None:
        raise RuntimeError("tray wiring broke")

    daemon.on_cloud_status = _boom
    monkeypatch.setattr(daemon, "_persist_entry", lambda updates: None)

    daemon._handle_fatal_auth()  # must not raise


def test_daemon_without_callback_is_unchanged(monkeypatch) -> None:
    """Cloud daemons launched outside the desktop shell have no callback —
    the notify hook must be a no-op, not an AttributeError."""
    daemon = MachineDaemon(api_key="test-key", base_url="http://localhost:0")
    assert daemon.on_cloud_status is None
    monkeypatch.setattr(daemon, "_persist_entry", lambda updates: None)

    daemon._handle_fatal_auth()  # must not raise


# ----------------------------------------------------------------------
# LocalListenerLifecycle: the local server owns the main thread; the cloud
# loop is a background attachment. Fatal cloud auth (or any cloud-loop exit)
# must leave /healthz serving — the Electron shell polls it to show
# "auth failed — sign in again" instead of crash-looping a vanished daemon.
# ----------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _healthz(port: int) -> dict | None:
    """Return the /healthz body, or None when nothing is listening."""
    try:
        return requests.get(f"http://127.0.0.1:{port}/healthz", timeout=1).json()
    except requests.RequestException:
        return None


def _wait_until(predicate: Callable[[], bool], timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


@contextmanager
def _running_lifecycle(
    cloud_daemon,
    store: LocalStore,
    *,
    app_daemon=None,
    cloud_status: CloudAuthStatus | None = None,
) -> Iterator[tuple[LocalListenerLifecycle, int]]:
    """Run the lifecycle on a worker thread against a real loopback socket.

    In production ``lifecycle.run()`` owns the main thread (uvicorn installs
    the SIGTERM/SIGINT handlers there); here it runs off-main so uvicorn
    skips handler installation and the teardown flips ``should_exit``
    directly — exactly what those handlers do on SIGTERM.
    """
    port = _free_port()
    app = create_local_app(
        daemon=app_daemon if app_daemon is not None else cloud_daemon,
        store=store,
        nonce=NONCE,
        allowed_origin=ORIGIN,
        local_only=False,
        terminal=TerminalService(),
        cloud_status=cloud_status,
    )
    server = _local_uvicorn_server(app, port)
    lifecycle = LocalListenerLifecycle(
        daemon=cloud_daemon, server=server, join_timeout=2.0
    )
    main = threading.Thread(target=lifecycle.run, name="listener-main", daemon=True)
    main.start()
    try:
        assert _wait_until(lambda: _healthz(port) is not None), (
            "local server never came up"
        )
        yield lifecycle, port
    finally:
        server.should_exit = True  # SIGTERM-equivalent: what handle_exit sets
        main.join(timeout=10)
    assert not main.is_alive(), "listener main thread failed to shut down"


def test_fatal_cloud_auth_keeps_local_server_serving(
    monkeypatch: pytest.MonkeyPatch, store: LocalStore
) -> None:
    """A revoked credential ends the cloud loop but NOT the local server.

    Runs the REAL MachineDaemon fatal-auth path (401 -> AuthenticationError
    -> run() flags _fatal_auth -> _handle_fatal_auth -> on_cloud_status) and
    pins the desktop contract: after the cloud thread is dead, /healthz still
    answers with cloud="auth_failed" instead of connection-refused.
    """
    daemon = MachineDaemon(api_key="revoked-key", base_url="http://localhost:9")
    status = CloudAuthStatus()
    daemon.on_cloud_status = status.set
    monkeypatch.setattr(daemon, "_persist_entry", lambda updates: None)

    def _register_401() -> None:
        raise AuthenticationError("agent server rejected credentials (401)")

    monkeypatch.setattr(daemon, "register_machine", _register_401)

    with _running_lifecycle(daemon, store, cloud_status=status) as (lifecycle, port):
        cloud_thread = lifecycle.cloud_thread
        assert cloud_thread is not None
        cloud_thread.join(timeout=5)
        assert not cloud_thread.is_alive(), "cloud loop should exit on fatal auth"
        assert daemon.fatal_auth is True

        # AFTER the cloud loop returned, the local server must still answer.
        body = _healthz(port)
        assert body is not None, "local server died with the cloud loop"
        assert body["status"] == "ok"
        assert body["mode"] == "cloud"
        assert body["cloud"] == "auth_failed"

    # Shell quit (SIGTERM-equivalent) tears the listener down for real.
    assert _wait_until(lambda: _healthz(port) is None)


class _CrashingCloudDaemon:
    """Cloud loop that dies unexpectedly — still must not kill the listener."""

    fatal_auth = False

    def run(self) -> None:
        raise RuntimeError("cloud loop blew up")

    def request_stop(self) -> None:  # pragma: no cover - loop already dead
        pass


def test_cloud_loop_crash_keeps_local_server_serving(store: LocalStore) -> None:
    """Any cloud-loop exit — not just fatal auth — leaves the listener up."""
    cloud = _CrashingCloudDaemon()
    with _running_lifecycle(
        cloud, store, app_daemon=_StubDaemon(), cloud_status=CloudAuthStatus()
    ) as (lifecycle, port):
        cloud_thread = lifecycle.cloud_thread
        assert cloud_thread is not None
        cloud_thread.join(timeout=5)
        assert not cloud_thread.is_alive()

        body = _healthz(port)
        assert body is not None, "local server died with the cloud loop"
        assert body["status"] == "ok"


class _BlockingCloudDaemon:
    """Stands in for a healthy cloud loop that only exits when asked."""

    fatal_auth = False

    def __init__(self) -> None:
        self._stop = threading.Event()
        self.stop_requested = False

    def run(self) -> None:
        self._stop.wait(timeout=10)

    def request_stop(self) -> None:
        self.stop_requested = True
        self._stop.set()


def test_shutdown_stops_and_joins_cloud_thread(store: LocalStore) -> None:
    """SIGTERM path: server exits first, then the cloud thread is stopped and
    joined (bounded) so daemon cleanup runs and nothing is orphaned."""
    cloud = _BlockingCloudDaemon()
    with _running_lifecycle(cloud, store, app_daemon=_StubDaemon()) as (
        lifecycle,
        port,
    ):
        assert lifecycle.cloud_thread is not None
        assert lifecycle.cloud_thread.is_alive()

    assert cloud.stop_requested is True
    assert lifecycle.cloud_thread is not None
    assert not lifecycle.cloud_thread.is_alive()
    assert _wait_until(lambda: _healthz(port) is None)


# ----------------------------------------------------------------------
# MachineDaemon.request_stop(): the hook LocalListenerLifecycle uses to break
# the (otherwise endless) WS reconnect loop from another thread. Inert on the
# plain `vicoa daemon` path, which never calls it.
# ----------------------------------------------------------------------
class _FakeWsClient:
    """Blocks like the real reconnect loop until ``stop()`` is called."""

    def __init__(self, **_kwargs: object) -> None:
        self._stop = threading.Event()

    def run(self) -> None:
        self._stop.wait(timeout=10)

    def stop(self) -> None:
        self._stop.set()


def _ws_daemon_with_stubbed_cloud(monkeypatch: pytest.MonkeyPatch) -> MachineDaemon:
    daemon = MachineDaemon(
        api_key="test-key",
        base_url="http://localhost:9",
        ws_url="ws://localhost:9/ws",
    )
    daemon.machine_id = "mac-1"
    monkeypatch.setattr(daemon, "register_machine", lambda: None)
    monkeypatch.setattr(daemon, "send_heartbeat", lambda: None)
    monkeypatch.setattr(daemon, "_catchup_poll", lambda: None)
    monkeypatch.setattr("vicoa.machine_daemon.SpawnRequestWsClient", _FakeWsClient)
    return daemon


def test_request_stop_unblocks_running_ws_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daemon = _ws_daemon_with_stubbed_cloud(monkeypatch)
    thread = threading.Thread(target=daemon.run, daemon=True)
    thread.start()
    assert _wait_until(lambda: daemon._ws_client is not None)

    daemon.request_stop()
    thread.join(timeout=5)

    assert not thread.is_alive(), "run() did not return after request_stop()"
    assert daemon.fatal_auth is False  # a stop is not a fatal-auth exit


def test_request_stop_before_ws_loop_still_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stop landing while register/heartbeat are still running must be
    honoured when the WS loop starts, not lost."""
    daemon = _ws_daemon_with_stubbed_cloud(monkeypatch)
    daemon.request_stop()

    thread = threading.Thread(target=daemon.run, daemon=True)
    thread.start()
    thread.join(timeout=5)

    assert not thread.is_alive(), "pre-set stop request was lost"
    assert daemon.fatal_auth is False
