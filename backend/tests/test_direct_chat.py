"""Helpers used by the minimal direct-mode client (`scripts/direct_chat.py`)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "direct_chat.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("direct_chat", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_http_base_from_ws() -> None:
    module = _load()
    assert module.http_base_from_ws("ws://127.0.0.1:8765/ws") == (
        "http://127.0.0.1:8765"
    )
    assert module.http_base_from_ws("wss://host.tailnet.ts.net/ws") == (
        "https://host.tailnet.ts.net"
    )


def test_queue_status_from_metadata() -> None:
    module = _load()
    assert module.queue_status({"queue": {"status": "consumed"}}) == "consumed"
    assert module.queue_status({"queue": {"status": "steer"}}) == "steer"
    assert module.queue_status({}) is None
    assert module.queue_status(None) is None
