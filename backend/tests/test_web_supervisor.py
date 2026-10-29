"""Next process discovery and supervision for direct mode."""

from __future__ import annotations

import socket
import sys
import time
from pathlib import Path

import pytest

from vicoa.local_server.web_supervisor import (
    WebSupervisor,
    find_web_command,
    wait_for_web,
)


def test_find_standalone_command(tmp_path: Path) -> None:
    web_dir = tmp_path / "web"
    standalone = web_dir / ".next" / "standalone"
    standalone.mkdir(parents=True)
    (standalone / "server.js").write_text("// server\n")
    (web_dir / ".next" / "static").mkdir()
    (web_dir / ".next" / "static" / "app.js").write_text("// app\n")
    (web_dir / "public").mkdir()
    (web_dir / "public" / "logo.svg").write_text("<svg/>")

    command, cwd = find_web_command(web_dir, 3010)

    assert command[0].endswith("node") or command[0] == "node"
    assert command[1] == "server.js"
    assert cwd == standalone
    assert (standalone / ".next" / "static" / "app.js").exists()
    assert (standalone / "public" / "logo.svg").exists()


def test_find_standalone_command_refreshes_existing_assets(
    tmp_path: Path,
) -> None:
    web_dir = tmp_path / "web"
    standalone = web_dir / ".next" / "standalone"
    stale_static = standalone / ".next" / "static"
    stale_public = standalone / "public"
    stale_static.mkdir(parents=True)
    stale_public.mkdir()
    (standalone / "server.js").write_text("// server\n")
    (stale_static / "old.js").write_text("// old\n")
    (stale_public / "sw.js").write_text("// old worker\n")
    (web_dir / ".next" / "static").mkdir()
    (web_dir / ".next" / "static" / "new.js").write_text("// new\n")
    (web_dir / "public").mkdir()
    (web_dir / "public" / "sw.js").write_text("// new worker\n")

    find_web_command(web_dir, 3010)

    assert (stale_static / "new.js").read_text() == "// new\n"
    assert (stale_public / "sw.js").read_text() == "// new worker\n"


def test_find_next_start_command(tmp_path: Path) -> None:
    web_dir = tmp_path / "web"
    (web_dir / "node_modules" / ".bin").mkdir(parents=True)
    next_bin = web_dir / "node_modules" / ".bin" / "next"
    next_bin.write_text("#!/bin/sh\n")
    next_bin.chmod(0o755)
    (web_dir / ".next").mkdir()
    (web_dir / ".next" / "BUILD_ID").write_text("build")

    command, cwd = find_web_command(web_dir, 3010)

    assert command == [
        str(next_bin),
        "start",
        "-p",
        "3010",
        "-H",
        "127.0.0.1",
    ]
    assert cwd == web_dir


def test_find_web_command_requires_a_build(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="no built Next app"):
        find_web_command(tmp_path, 3010)


def test_wait_for_web_times_out_on_a_closed_port() -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    assert wait_for_web(f"http://127.0.0.1:{port}", timeout=0.3) is False


def test_supervisor_restarts_a_wedged_process(tmp_path: Path) -> None:
    supervisor = WebSupervisor(
        web_dir=tmp_path,
        port=3998,
        upstream="http://127.0.0.1:3998",
        log_path=tmp_path / "wedged.log",
        command=[sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=tmp_path,
    )
    supervisor.start()
    first = supervisor._process
    assert first is not None
    supervisor._started_at = time.monotonic() - 20
    supervisor._unhealthy_since = time.monotonic() - 10

    assert supervisor.ensure_running() is True

    assert first.poll() is not None
    assert supervisor._process is not None
    assert supervisor._process is not first
    supervisor.stop()


def test_supervisor_starts_restarts_and_stops(tmp_path: Path) -> None:
    supervisor = WebSupervisor(
        web_dir=tmp_path,
        port=3999,
        upstream="http://127.0.0.1:3999",
        log_path=tmp_path / "next.log",
        command=[sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=tmp_path,
    )

    supervisor.start()
    assert supervisor.running is True
    first = supervisor._process
    assert first is not None

    first.terminate()
    first.wait(timeout=5)
    supervisor._restart_at = 0.0
    assert supervisor.ensure_running() is True
    assert supervisor.running is True
    second = supervisor._process
    assert second is not None and second.pid != first.pid

    supervisor.stop()
    assert supervisor.running is False
    time.sleep(0.1)
    assert (tmp_path / "next.log").exists()
