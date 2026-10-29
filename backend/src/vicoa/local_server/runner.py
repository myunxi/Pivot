"""Boot the local server thread and the daemon around it.

Two entry points, both called from ``vicoa.cli.cmd_machine_daemon``:

* :func:`run_local_only_daemon` — logged-out desktop. No credentials, no
  machine registration, no heartbeats, no cloud WebSocket, no spawn-request
  polling. A ``MachineDaemon`` is still constructed — pointed at the local
  server itself (``base_url=http://127.0.0.1:<port>``, ``api_key=<nonce>``) —
  purely as the RPC handler + headless-spawn factory, so ``spawn-session``
  launches children whose SDK talks to the local server. ``daemon.run()`` is
  never called.

* :func:`run_daemon_with_local_listener` — logged-in desktop. Exactly today's
  credentialed daemon (register/heartbeat/cloud WS) plus the local server for
  RPC/pty; the local store stays empty because chat traffic flows to the
  cloud. The local server owns the MAIN thread and the cloud loop runs on a
  background thread (see :class:`LocalListenerLifecycle`) so a dead cloud
  credential never tears down the listener the desktop shell depends on.
"""

from __future__ import annotations

import logging
import os
import secrets
import signal
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import Any, Protocol
from urllib.parse import urlparse

import uvicorn
from fastapi import FastAPI

from vicoa.machine_daemon import (
    DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    DEFAULT_POLL_INTERVAL_SECONDS,
    MachineDaemon,
)
from vicoa.terminal.service import TerminalService
from vicoa.utils import derive_ws_url

from .app import CloudAuthStatus, create_local_app
from .store import LocalStore
from .web_supervisor import (
    DEFAULT_WEB_PORT,
    WebSupervisor,
    default_web_dir,
    wait_for_web,
)

logger = logging.getLogger(__name__)

DEFAULT_DIRECT_STORE_PATH = Path.home() / ".vicoa" / "direct" / "store.db"
DEFAULT_DIRECT_TOKEN_PATH = Path.home() / ".vicoa" / "direct" / "token"


@dataclass(slots=True)
class LocalListenerSettings:
    """Local-listener flags resolved by the CLI (port, nonce, origin, mode)."""

    port: int
    nonce: str
    allowed_origin: str
    local_only: bool
    public_origin: str | None = None
    web_upstream: str | None = None


@dataclass(slots=True)
class DirectRuntime:
    """Everything a no-cloud direct-mode process needs to serve one machine."""

    daemon: MachineDaemon
    store: LocalStore
    settings: LocalListenerSettings
    app: FastAPI
    store_path: Path


def local_base_url(port: int) -> str:
    return f"http://127.0.0.1:{port}"


def _normalize_public_origin(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"--public-url must be an absolute http(s) URL, got {value!r}")
    return f"{parsed.scheme}://{parsed.netloc}"


def load_or_create_direct_token(
    *,
    token_path: str | Path | None = None,
    explicit: str | None = None,
    rotate: bool = False,
) -> tuple[str, bool, Path]:
    """Return ``(token, created, path)`` for direct-mode credentials."""
    path = (
        Path(token_path).expanduser()
        if token_path is not None
        else DEFAULT_DIRECT_TOKEN_PATH
    )
    if explicit:
        return explicit, False, path

    if rotate:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    if path.exists():
        token = path.read_text(encoding="utf-8").strip()
        if token:
            return token, False, path

    token = secrets.token_hex(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return token, True, path


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
        return True


def _wait_for_listener(
    port: int, thread: threading.Thread, *, timeout: float = 5.0
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not thread.is_alive():
            return False
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.2)
            try:
                probe.connect(("127.0.0.1", port))
                return True
            except OSError:
                time.sleep(0.05)
    return False


# How long shutdown waits for the cloud daemon thread to finish its own
# cleanup (WS CLOSE frame, heartbeat stop) before giving up so quit can't hang.
DAEMON_JOIN_TIMEOUT_SECONDS = 5.0


class _LocalUvicornServer(uvicorn.Server):
    """uvicorn re-raises a captured SIGTERM/SIGINT with the default handler
    restored once graceful shutdown finishes (``Server.capture_signals``) —
    that would kill the process the moment ``server.run()`` unwinds, before
    :class:`LocalListenerLifecycle` gets to stop and join the cloud daemon
    thread. The desktop shell only needs a clean exit, so swallow the
    captured signals and let ``run()`` return normally (exit code 0).

    Off the main thread (``start_local_server_thread``) uvicorn never
    installs handlers, so this override is inert there.
    """

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        super().handle_exit(sig, frame)
        captured = getattr(self, "_captured_signals", None)
        if isinstance(captured, list):
            captured.clear()


def _local_uvicorn_server(app: FastAPI, port: int) -> uvicorn.Server:
    return _LocalUvicornServer(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            access_log=False,
        )
    )


def start_local_server_thread(
    app: FastAPI, port: int
) -> tuple[uvicorn.Server, threading.Thread]:
    """Run uvicorn on 127.0.0.1:<port> in a daemon thread.

    uvicorn skips signal-handler installation off the main thread, so
    ``Server.run`` is thread-safe here; the thread dies with the process.
    """
    server = _local_uvicorn_server(app, port)
    thread = threading.Thread(target=server.run, name="vicoa-local-server", daemon=True)
    thread.start()
    return server, thread


class CloudDaemonHandle(Protocol):
    """The slice of :class:`vicoa.machine_daemon.MachineDaemon` the listener
    lifecycle drives (kept structural so tests can substitute fakes)."""

    @property
    def fatal_auth(self) -> bool: ...

    def run(self) -> None: ...

    def request_stop(self) -> None: ...


class LocalListenerLifecycle:
    """Thread layout for ``vicoa daemon --local-listener`` (logged-in desktop).

    The LOCAL server owns the MAIN thread and the cloud daemon loop runs on a
    background thread — the inverse of a plain ``vicoa daemon``:

    * The local listener is the desktop app's lifeline; the cloud link is an
      optional background attachment. Whatever ends the cloud loop — fatal
      auth (revoked key), a crash, a clean return — the process stays up
      serving ``/healthz`` (``cloud: "auth_failed"`` after a dead credential)
      so the Electron shell can prompt re-sign-in instead of crash-looping a
      vanished daemon. A headless ``vicoa daemon`` keeps its opposite
      contract: it still exits on fatal auth.
    * uvicorn installs its SIGINT/SIGTERM handlers only when ``Server.run()``
      executes on the main thread, so the shell's SIGTERM lands as a graceful
      shutdown; ``MachineDaemon.run()`` installs no signal handlers of its
      own, so it is safe on the background thread.

    Shutdown: when ``server.run()`` returns, ``daemon.request_stop()`` breaks
    the cloud WS loop and the thread is joined with a bounded timeout so quit
    can never hang (the thread is ``daemon=True`` as a backstop).
    """

    def __init__(
        self,
        *,
        daemon: CloudDaemonHandle,
        server: uvicorn.Server,
        join_timeout: float = DAEMON_JOIN_TIMEOUT_SECONDS,
    ) -> None:
        self.daemon = daemon
        self.server = server
        self.join_timeout = join_timeout
        self.cloud_thread: threading.Thread | None = None

    def run(self) -> None:
        """Serve the listener until SIGTERM/SIGINT, then stop the cloud loop."""
        self._start_cloud_loop()
        try:
            self.server.run()
        finally:
            self._stop_cloud_loop()

    def _start_cloud_loop(self) -> None:
        thread = threading.Thread(
            target=self._run_cloud_loop, name="vicoa-cloud-daemon", daemon=True
        )
        self.cloud_thread = thread
        thread.start()

    def _run_cloud_loop(self) -> None:
        """Run the cloud daemon; its exit — any exit — never stops the server."""
        try:
            self.daemon.run()
        except Exception:
            logger.exception(
                "cloud daemon loop crashed; local server stays up for the shell"
            )
            return
        if self.daemon.fatal_auth:
            logger.info(
                "cloud credential rejected; local server stays up so /healthz "
                "reports auth_failed until the user signs in again"
            )
        else:
            logger.info("cloud daemon loop ended; local server stays up")

    def _stop_cloud_loop(self) -> None:
        thread = self.cloud_thread
        if thread is None or not thread.is_alive():
            return
        try:
            self.daemon.request_stop()
        except Exception:
            logger.exception("cloud daemon stop request failed; joining anyway")
        thread.join(timeout=self.join_timeout)
        if thread.is_alive():
            logger.warning(
                "cloud daemon thread still running %.1fs after shutdown; "
                "exiting without it",
                self.join_timeout,
            )


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    # The health probe and proxy make one request per few seconds; httpx's
    # INFO line for each would drown the direct-mode startup log.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def _build_local_app(
    *,
    daemon: MachineDaemon,
    settings: LocalListenerSettings,
    store: LocalStore,
    cloud_status: CloudAuthStatus | None = None,
) -> FastAPI:
    terminal = TerminalService()
    return create_local_app(
        daemon=daemon,
        store=store,
        nonce=settings.nonce,
        allowed_origin=settings.allowed_origin,
        local_only=settings.local_only,
        terminal=terminal,
        cloud_status=cloud_status,
        extra_allowed_origins=(
            [settings.public_origin] if settings.public_origin else None
        ),
        web_upstream=settings.web_upstream,
    )


def build_direct_runtime(
    *,
    port: int,
    store_path: str | Path | None = None,
    nonce: str | None = None,
    allowed_origin: str | None = None,
    public_url: str | None = None,
    web_upstream: str | None = None,
    cli_version: str | None = None,
) -> DirectRuntime:
    """Build the local server + store without registering with the cloud."""
    port = int(port)
    if not 0 < port < 65536:
        raise ValueError(f"invalid direct-mode port: {port!r}")

    resolved_nonce = nonce or secrets.token_hex(32)
    resolved_origin = allowed_origin or local_base_url(port)
    public_origin = _normalize_public_origin(public_url) if public_url else None
    resolved_store_path = (
        Path(store_path).expanduser()
        if store_path is not None
        else DEFAULT_DIRECT_STORE_PATH
    )

    # A leftover cloud override would point spawned runners at the cloud.
    os.environ.pop("VICOA_WS_URL", None)

    settings = LocalListenerSettings(
        port=port,
        nonce=resolved_nonce,
        allowed_origin=resolved_origin,
        local_only=True,
        public_origin=public_origin,
        web_upstream=web_upstream,
    )
    daemon = MachineDaemon(
        api_key=resolved_nonce,
        base_url=local_base_url(port),
        cli_version=cli_version,
    )
    store = LocalStore(resolved_store_path)
    app = _build_local_app(daemon=daemon, settings=settings, store=store)
    return DirectRuntime(
        daemon=daemon,
        store=store,
        settings=settings,
        app=app,
        store_path=resolved_store_path,
    )


def run_direct_daemon(
    *,
    port: int = 8765,
    store_path: str | Path | None = None,
    nonce: str | None = None,
    allowed_origin: str | None = None,
    public_url: str | None = None,
    token_path: str | Path | None = None,
    rotate_token: bool = False,
    show_token: bool = False,
    with_web: bool = False,
    web_dir: str | Path | None = None,
    web_port: int = DEFAULT_WEB_PORT,
    web_upstream: str | None = None,
    cli_version: str | None = None,
) -> None:
    """Serve one machine locally: no login, no cloud relay, no daemon.run()."""
    _configure_logging()
    # Direct mode stores tokens, prompts, and attachments under
    # ~/.vicoa/direct. Make everything this process creates private, including
    # files written by the supervised Next server and headless runners.
    os.umask(0o077)
    port = int(port)
    if not _port_is_free(port):
        raise SystemExit(
            f"[direct] port {port} is already in use on 127.0.0.1; "
            "choose another --port"
        )

    try:
        public_origin = _normalize_public_origin(public_url) if public_url else None
    except ValueError as exc:
        raise SystemExit(f"[direct] {exc}") from exc

    token, _, resolved_token_path = load_or_create_direct_token(
        token_path=token_path,
        explicit=nonce,
        rotate=rotate_token,
    )

    web_port = int(web_port)
    resolved_web_upstream = (web_upstream or f"http://127.0.0.1:{web_port}").rstrip("/")
    supervisor: WebSupervisor | None = None
    if with_web:
        resolved_web_dir = Path(web_dir).expanduser() if web_dir else default_web_dir()
        try:
            supervisor = WebSupervisor(
                web_dir=resolved_web_dir,
                port=web_port,
                upstream=resolved_web_upstream,
            )
        except FileNotFoundError as exc:
            raise SystemExit(f"[direct] {exc}") from exc
        supervisor.start()

    proxy_upstream = resolved_web_upstream if (with_web or web_upstream) else None
    runtime = build_direct_runtime(
        port=port,
        store_path=store_path,
        nonce=token,
        allowed_origin=allowed_origin,
        public_url=public_origin,
        web_upstream=proxy_upstream,
        cli_version=cli_version,
    )
    server, thread = start_local_server_thread(runtime.app, port)
    if not _wait_for_listener(port, thread):
        server.should_exit = True
        thread.join(timeout=2.0)
        runtime.store.close()
        raise SystemExit(
            f"[direct] failed to start the listener on {local_base_url(port)}"
        )

    print_token = show_token or nonce is not None
    print(f"[direct] listening on {local_base_url(port)}", flush=True)
    if print_token:
        print(f"[direct] token: {token}", flush=True)
    else:
        print(
            f"[direct] token: stored at {resolved_token_path} "
            "(use --show-token to print)",
            flush=True,
        )
    if public_origin:
        print(f"[direct] public origin: {public_origin}", flush=True)
        if print_token:
            print(f"[direct] pair: {public_origin}/#token={token}", flush=True)
    print(f"[direct] store: {runtime.store_path}", flush=True)
    print("[direct] cloud: disabled", flush=True)
    if supervisor is not None:
        print(f"[direct] web: starting Next from {supervisor.cwd}", flush=True)
        if wait_for_web(resolved_web_upstream, timeout=60.0):
            print(f"[direct] web: {resolved_web_upstream}", flush=True)
        else:
            print(
                f"[direct] web: not ready at {resolved_web_upstream}; "
                "the proxy will retry as Next recovers",
                flush=True,
            )
    elif proxy_upstream is not None:
        print(f"[direct] web upstream: {proxy_upstream}", flush=True)

    stop = threading.Event()

    def _request_stop(sig: int, _frame: FrameType | None) -> None:
        print(f"\n[direct] received signal {sig}; shutting down", flush=True)
        stop.set()

    previous_handlers: dict[int, Any] = {}
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            previous_handlers[sig] = signal.signal(sig, _request_stop)
        except (ValueError, OSError):
            pass

    try:
        if supervisor is not None:
            while not stop.wait(timeout=1.0):
                supervisor.ensure_running()
        else:
            stop.wait()
    finally:
        for sig, handler in previous_handlers.items():
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):
                pass
        try:
            runtime.daemon.terminate_all_sessions()
        except Exception:
            logger.exception("direct shutdown: failed to stop session runners")
        if supervisor is not None:
            try:
                supervisor.stop()
            except Exception:
                logger.exception("direct shutdown: failed to stop Next")
        server.should_exit = True
        thread.join(timeout=5.0)
        runtime.store.close()


def run_local_only_daemon(
    *,
    settings: LocalListenerSettings,
    cli_version: str | None = None,
    store_path: str | None = None,
) -> None:
    """DISABLED: local (logged-out) mode. Login is required.

    The desktop app no longer runs without credentials, and no user session
    data may be created or stored in ``~/.vicoa/local_store.db``. The
    previous implementation is kept below, commented out, until local mode
    is removed for good.
    """
    raise SystemExit(
        "local-only mode is disabled — sign in and run the daemon with credentials."
    )
    # _configure_logging()
    # base_url = local_base_url(settings.port)
    #
    # # Children derive their WS URL from --base-url unless VICOA_WS_URL is
    # # exported in this environment — a leftover cloud override would point
    # # local-only sessions at the wrong server, so drop it.
    # if os.environ.pop("VICOA_WS_URL", None) is not None:
    #     logger.info(
    #         "local-only daemon: ignoring inherited VICOA_WS_URL (children use %s)",
    #         derive_ws_url(base_url),
    #     )
    #
    # daemon = MachineDaemon(
    #     api_key=settings.nonce,
    #     base_url=base_url,
    #     cli_version=cli_version,
    # )
    # store = LocalStore(store_path)
    # app = _build_local_app(daemon=daemon, settings=settings, store=store)
    # start_local_server_thread(app, settings.port)
    # print(
    #     f"[daemon] local-only server listening on {base_url} "
    #     f"(origin {settings.allowed_origin})"
    # )
    #
    # # No cloud loop to run in local-only mode — park the main thread until
    # # the Electron shell stops us (SIGTERM) or the user hits Ctrl-C.
    # threading.Event().wait()


def run_daemon_with_local_listener(
    *,
    api_key: str,
    base_url: str,
    settings: LocalListenerSettings,
    poll_interval: int = DEFAULT_POLL_INTERVAL_SECONDS,
    heartbeat_interval: int = DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    cli_version: str | None = None,
    ws_url: str | None = None,
    store_path: str | None = None,
) -> None:
    """Run the normal credentialed daemon plus the local listener.

    Mirrors ``vicoa.machine_daemon.run_daemon`` construction; the local store
    stays empty in this mode (chat flows through the cloud) — the local socket
    carries RPC (files/git/pty) and empty fetch results.

    Unlike a plain ``vicoa daemon``, the process does NOT exit when the cloud
    loop ends (fatal auth included): the local server keeps the main thread
    and keeps serving ``/healthz`` until the shell sends SIGTERM. See
    :class:`LocalListenerLifecycle`.
    """
    _configure_logging()
    daemon = MachineDaemon(
        api_key=api_key,
        base_url=base_url,
        poll_interval=poll_interval,
        heartbeat_interval=heartbeat_interval,
        cli_version=cli_version,
        ws_url=ws_url or os.getenv("VICOA_WS_URL") or derive_ws_url(base_url),
    )
    # Shared cloud-auth status: the daemon thread writes it (register success
    # → connected, fatal auth → auth_failed) and the local server thread reads
    # it for /healthz, so the desktop tray can tell starting / connected /
    # auth_failed apart.
    cloud_status = CloudAuthStatus()
    daemon.on_cloud_status = cloud_status.set
    # Never create/persist user data in ~/.vicoa/local_store.db: chat flows
    # through the cloud in this mode, and the store only backs the local
    # socket's fetch/RPC shims (empty results, recent-directories) — keep it
    # in memory. (Local mode itself is disabled; see run_local_only_daemon.)
    # store = LocalStore(store_path)
    store = LocalStore(":memory:")
    app = _build_local_app(
        daemon=daemon, settings=settings, store=store, cloud_status=cloud_status
    )
    print(
        f"[daemon] local listener on {local_base_url(settings.port)} "
        f"(origin {settings.allowed_origin})"
    )
    server = _local_uvicorn_server(app, settings.port)
    LocalListenerLifecycle(daemon=daemon, server=server).run()
