"""Start and supervise the Next web server for direct mode.

The daemon proxies non-API HTTP to Next; when ``--with-web`` is used it also
owns the Next process (start, restart with backoff, stop on shutdown). The
standalone build (``.next/standalone/server.js``) is preferred; a normal
``next start`` build is the fallback.
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

DEFAULT_WEB_PORT = 3000
DEFAULT_WEB_LOG_PATH = Path.home() / ".vicoa" / "direct" / "logs" / "next.log"
_RESTART_MAX_SECONDS = 30.0
_STABLE_AFTER_SECONDS = 60.0
_HEALTH_GRACE_SECONDS = 10.0
_HEALTH_PROBE_INTERVAL_SECONDS = 5.0
_UNHEALTHY_RESTART_SECONDS = 5.0


def default_web_dir() -> Path:
    candidates = [
        Path.cwd() / "apps" / "web",
        Path(__file__).resolve().parents[4] / "apps" / "web",
    ]
    for candidate in candidates:
        if (candidate / ".next").exists():
            return candidate
    return candidates[0]


def _copy_standalone_assets(web_dir: Path) -> None:
    standalone = web_dir / ".next" / "standalone"
    pairs = [
        (web_dir / ".next" / "static", standalone / ".next" / "static"),
        (web_dir / "public", standalone / "public"),
    ]
    for source, target in pairs:
        if source.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            # Overwrite stale files: a rebuild changes public/ (manifest,
            # service worker, icons) and adds new hashed _next/static chunks
            # without necessarily clearing the standalone copy.
            shutil.copytree(source, target, dirs_exist_ok=True)


def find_web_command(web_dir: Path, port: int) -> tuple[list[str], Path]:
    """Return ``(command, cwd)`` for the built Next app."""
    standalone = web_dir / ".next" / "standalone" / "server.js"
    if standalone.exists():
        _copy_standalone_assets(web_dir)
        return [shutil.which("node") or "node", "server.js"], standalone.parent

    next_bin = web_dir / "node_modules" / ".bin" / "next"
    if next_bin.exists() and (web_dir / ".next" / "BUILD_ID").exists():
        return (
            [str(next_bin), "start", "-p", str(port), "-H", "127.0.0.1"],
            web_dir,
        )

    raise FileNotFoundError(
        f"no built Next app in {web_dir}; run `NEXT_PUBLIC_VICOA_DIRECT=1 pnpm build` "
        "there, or pass --web-dir / --web-upstream"
    )


def wait_for_web(upstream: str, *, timeout: float = 60.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            # Loopback health probe: ignore system HTTP proxies the same way the
            # reverse proxy does, or a proxy can answer/hang for localhost.
            response = httpx.get(
                upstream, timeout=1.0, follow_redirects=True, trust_env=False
            )
            if response.status_code < 500:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    return False


def _terminate_process(process: subprocess.Popen[bytes], *, timeout: float) -> None:
    if process.poll() is not None:
        return
    pgid: int | None = None
    if os.name == "posix":
        try:
            candidate = os.getpgid(process.pid)
            if candidate != os.getpgid(0):
                pgid = candidate
        except OSError:
            pgid = None
    try:
        if pgid is not None:
            os.killpg(pgid, signal.SIGTERM)
        else:
            process.terminate()
    except (ProcessLookupError, OSError):
        pass
    try:
        process.wait(timeout=timeout)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        if pgid is not None:
            os.killpg(pgid, signal.SIGKILL)
        else:
            process.kill()
    except (ProcessLookupError, OSError):
        pass
    try:
        process.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        pass


class WebSupervisor:
    def __init__(
        self,
        *,
        web_dir: Path,
        port: int = DEFAULT_WEB_PORT,
        upstream: str | None = None,
        log_path: Path = DEFAULT_WEB_LOG_PATH,
        command: list[str] | None = None,
        cwd: Path | None = None,
    ) -> None:
        self.web_dir = Path(web_dir)
        self.port = int(port)
        self.upstream = (upstream or f"http://127.0.0.1:{self.port}").rstrip("/")
        self.log_path = Path(log_path)
        if command is None:
            command, resolved_cwd = find_web_command(self.web_dir, self.port)
        else:
            resolved_cwd = cwd or self.web_dir
        self.command = command
        self.cwd = resolved_cwd
        self._process: subprocess.Popen[bytes] | None = None
        self._log = None
        self._started_at = 0.0
        self._attempts = 0
        self._restart_at = 0.0
        self._unhealthy_since: float | None = None
        self._last_probe = 0.0

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self) -> None:
        # A rebuild regenerates the standalone tree without its static assets;
        # refresh them on every spawn so a respawn serves the newest build.
        _copy_standalone_assets(self.web_dir)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log = open(self.log_path, "ab", buffering=0)
        env = os.environ.copy()
        env["PORT"] = str(self.port)
        env["HOSTNAME"] = "127.0.0.1"
        env.setdefault("NODE_ENV", "production")
        self._process = subprocess.Popen(
            self.command,
            cwd=self.cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=self._log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self._started_at = time.monotonic()
        self._unhealthy_since = None
        self._last_probe = 0.0
        self._attempts += 1

    def ensure_running(self) -> bool:
        if self.running:
            now = time.monotonic()
            if now - self._started_at > _STABLE_AFTER_SECONDS:
                self._attempts = 0
            if now - self._started_at < _HEALTH_GRACE_SECONDS:
                return True
            if now - self._last_probe < _HEALTH_PROBE_INTERVAL_SECONDS:
                return True
            self._last_probe = now
            if wait_for_web(self.upstream, timeout=0.5):
                self._unhealthy_since = None
                return True
            if self._unhealthy_since is None:
                self._unhealthy_since = now
                return True
            if now - self._unhealthy_since < _UNHEALTHY_RESTART_SECONDS:
                return True
            logger.warning(
                "Next web server stopped responding at %s; restarting",
                self.upstream,
            )
            process = self._process
            self._process = None
            if process is not None:
                _terminate_process(process, timeout=1.0)
            self._unhealthy_since = None
            self._restart_at = 0.0
        now = time.monotonic()
        if now < self._restart_at:
            return False
        delay = min(_RESTART_MAX_SECONDS, 2.0 ** max(0, self._attempts - 1))
        self._restart_at = now + delay
        self.start()
        return True

    def stop(self) -> None:
        process = self._process
        self._process = None
        if process is not None and process.poll() is None:
            _terminate_process(process, timeout=5.0)
        if self._log is not None:
            self._log.close()
            self._log = None
