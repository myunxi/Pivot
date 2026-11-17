"""Command line entry points: ``pivot serve`` and ``pivot cron``.

``serve`` owns the long-lived store and starts the cron pump inside the app's
lifespan. ``cron`` is the single-shot variant for launchd/systemd and prints
one JSON line per rule result so callers can log or assert on it.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from .client import PivotClient
from .config import PivotConfig
from .engine import CronResult, Engine
from .errors import ConfigError, PivotError, RulesError
from .model import RulesFile
from .rules import load_rules
from .store import PivotStore


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pivot", description="Local orchestration for Vicoa direct mode."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("serve", help="run the dispatch API and cron pump")
    serve.add_argument("--host", default=None, help="bind address")
    serve.add_argument("--port", type=int, default=None, help="listen port")
    serve.add_argument(
        "--no-pump", action="store_true", help="do not run the in-process cron pump"
    )

    commands.add_parser("cron", help="run one cron tick and exit")

    args = parser.parse_args(argv)

    try:
        config = PivotConfig.from_env()
        config.ensure_data_dir()
        token = config.read_token()
    except (ConfigError, OSError) as exc:
        print(f"pivot: {exc}", file=sys.stderr)
        return 1

    store = PivotStore(config.store_path, data_dir=config.data_dir)
    try:
        try:
            rules = _load_rules(config.rules_path)
        except RulesError as exc:
            print(f"pivot: {config.rules_path}: {exc}", file=sys.stderr)
            return 1
        engine = Engine(config, store, rules, PivotClient(config.daemon_url, token))
        if args.command == "serve":
            return _serve(config, engine, args)
        return _cron(engine)
    except PivotError as exc:
        print(f"pivot: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()


def _load_rules(path: Path) -> RulesFile:
    try:
        return load_rules(path)
    except FileNotFoundError:
        print(
            f"pivot: no rules file at {path}; starting with no rules",
            file=sys.stderr,
        )
        return RulesFile(version=1, agents={}, rules=())


def _cron(engine: Engine) -> int:
    results = engine.run_cron_tick()
    for result in results:
        _print_result(result)
    return 1 if any(result.status == "failed" for result in results) else 0


def _print_result(result: CronResult) -> None:
    print(json.dumps(asdict(result), ensure_ascii=False), flush=True)


def _serve(config: PivotConfig, engine: Engine, args: argparse.Namespace) -> int:
    import uvicorn

    from .api import CRON_PUMP_INTERVAL, create_serve_app

    host = args.host or config.serve_host
    port = args.port or config.serve_port
    app = create_serve_app(
        config,
        engine,
        pump_interval=None if args.no_pump else CRON_PUMP_INTERVAL,
    )
    print(
        f"pivot serve: http://{host}:{port} "
        f"(daemon {config.daemon_url}, rules {config.rules_path})",
        flush=True,
    )
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0
