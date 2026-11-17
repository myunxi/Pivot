"""The ``pivot serve`` FastAPI app.

Routes live under ``/v1`` and share one credential: the direct-mode nonce read
from ``config.read_token()``. The dashboard never talks to this port directly —
Next proxies ``/api/pivot/*`` same-origin.

Rules saving is validate-then-replace: the new bytes are written to a temp file
next to the real path, parsed from *that* file, and only then atomically moved
into place. A failure leaves both the disk and ``engine.rules`` untouched.
"""

from __future__ import annotations

import hmac
import logging
import os
import tempfile
import threading
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from .config import PivotConfig
from .engine import DispatchResult, Engine
from .errors import ConfigError, RulesError
from .model import TicketBody, TicketState
from .rules import load_rules

logger = logging.getLogger("pivot.api")

MAX_RULES_FILE_BYTES = 1024 * 1024
DEFAULT_TICKET_LIMIT = 30
CRON_PUMP_INTERVAL = 2.0


class RulesFileBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str


def _ticket_payload(value: Any) -> dict[str, Any]:
    return asdict(value)


def _event_payload(value: Any) -> dict[str, Any]:
    payload = asdict(value)
    # The wire contract (and the dashboard's ``PivotEvent``) calls the event
    # kind ``type``; the stored dataclass field is ``event_type``.
    payload["type"] = payload.pop("event_type")
    return payload


def _dispatch_payload(result: DispatchResult) -> dict[str, Any]:
    payload: dict[str, Any] = {"status": result.status, "event": result.event_id}
    if result.ticket is not None:
        payload["ticket"] = _ticket_payload(result.ticket)
    if result.reason is not None:
        payload["reason"] = result.reason
    if result.error is not None:
        payload["error"] = result.error
    return payload


def create_serve_app(
    config: PivotConfig,
    engine: Engine,
    *,
    pump_interval: float | None = None,
) -> FastAPI:
    """Build the orchestration app. ``pump_interval`` starts the cron thread."""

    def require_auth(authorization: str | None = Header(default=None)) -> None:
        if authorization is None or not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="unauthorized")
        supplied = authorization.split(" ", 1)[1].strip()
        try:
            expected = config.read_token()
        except ConfigError as exc:
            raise HTTPException(status_code=401, detail="unauthorized") from exc
        if not hmac.compare_digest(supplied, expected):
            raise HTTPException(status_code=401, detail="unauthorized")

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        stop = threading.Event()
        thread: threading.Thread | None = None
        if pump_interval is not None and pump_interval > 0:
            thread = threading.Thread(
                target=_cron_pump,
                args=(engine, stop, pump_interval),
                name="pivot-cron-pump",
                daemon=True,
            )
            thread.start()
        try:
            yield
        finally:
            stop.set()
            if thread is not None:
                thread.join(timeout=5)

    app = FastAPI(title="Pivot orchestration", version="0.1.0", lifespan=lifespan)
    router = APIRouter(prefix="/v1", dependencies=[Depends(require_auth)])

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    # ------------------------------------------------------------------
    # Rules
    # ------------------------------------------------------------------
    @router.get("/rules")
    def get_rules() -> dict[str, Any]:
        return {"rules": engine.rules.rules_payload()}

    @router.get("/rules/file")
    def get_rules_file() -> Any:
        path = config.rules_path.expanduser()
        try:
            content = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return JSONResponse(
                status_code=404, content={"error": f"rules file not found: {path}"}
            )
        except OSError as exc:
            return JSONResponse(
                status_code=500,
                content={"error": f"cannot read rules file {path}: {exc}"},
            )
        return {"path": str(path), "content": content}

    @router.post("/rules/file")
    def save_rules_file(body: RulesFileBody) -> Any:
        size = len(body.content.encode("utf-8"))
        if size > MAX_RULES_FILE_BYTES:
            return JSONResponse(
                status_code=400,
                content={
                    "error": f"rules file is {size} bytes, "
                    f"limit is {MAX_RULES_FILE_BYTES}"
                },
            )

        path = config.rules_path.expanduser()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, raw_tmp = tempfile.mkstemp(
                prefix=".rules-", suffix=".json", dir=path.parent
            )
        except OSError as exc:
            return JSONResponse(
                status_code=500,
                content={"error": f"cannot write rules file {path}: {exc}"},
            )

        tmp = Path(raw_tmp)
        replaced = False
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(body.content)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                rules = load_rules(tmp)
            except RulesError as exc:
                # Show the real path, never the .rules-XXXX temp file.
                return JSONResponse(
                    status_code=400,
                    content={"error": str(exc).replace(str(tmp), str(path))},
                )
            try:
                os.chmod(tmp, path.stat().st_mode & 0o777)
            except FileNotFoundError:
                pass
            os.replace(tmp, path)
            replaced = True
        except OSError as exc:
            return JSONResponse(
                status_code=500,
                content={"error": f"cannot write rules file {path}: {exc}"},
            )
        finally:
            if not replaced:
                tmp.unlink(missing_ok=True)

        engine.rules = rules
        engine.store.audit(
            actor="dashboard",
            action="rules.updated",
            detail=f"{len(rules.rules)} rule(s)",
        )
        return {"path": str(path), "rules": rules.rules_payload()}

    # ------------------------------------------------------------------
    # Tickets
    # ------------------------------------------------------------------
    @router.post("/tickets")
    def create_ticket(body: TicketBody) -> Any:
        try:
            result = engine.dispatch(body)
        except Exception as exc:  # noqa: BLE001 - report a clean 500 body
            logger.exception("dispatch failed")
            return JSONResponse(status_code=500, content={"error": str(exc)})
        status_code = 500 if result.status == "failed" else 200
        return JSONResponse(status_code=status_code, content=_dispatch_payload(result))

    @router.get("/tickets")
    def list_tickets(
        state: str | None = None, limit: int = DEFAULT_TICKET_LIMIT
    ) -> Any:
        state_value: str | None = None
        if state is not None:
            try:
                state_value = TicketState(state).value
            except ValueError:
                return JSONResponse(
                    status_code=400, content={"error": f"unknown state: {state}"}
                )
        if limit < 1:
            return JSONResponse(
                status_code=400, content={"error": "limit must be a positive integer"}
            )
        tickets = engine.store.list_tickets(state=state_value, limit=limit)
        return {"tickets": [_ticket_payload(ticket) for ticket in tickets]}

    @router.get("/tickets/{ticket_id}")
    def get_ticket(ticket_id: str) -> Any:
        ticket = engine.store.get_ticket(ticket_id)
        if ticket is None:
            return JSONResponse(
                status_code=404,
                content={"error": f"ticket not found: {ticket_id}"},
            )
        event = engine.store.get_event(ticket.event_id)
        return {
            "ticket": _ticket_payload(ticket),
            "event": None if event is None else _event_payload(event),
            "runs": [asdict(run) for run in engine.store.list_runs(ticket.id)],
            "audit": [asdict(entry) for entry in engine.store.list_audit(ticket.id)],
            # Reserved: the dispatch UI does not read a receipt yet.
            "receipt": None,
        }

    @router.delete("/tickets/{ticket_id}")
    def delete_ticket(ticket_id: str) -> Any:
        result = engine.delete_ticket(ticket_id)
        if not result.deleted:
            return JSONResponse(
                status_code=404,
                content={"error": f"ticket not found: {ticket_id}"},
            )
        return {
            "id": result.ticket_id,
            "deleted": result.deleted,
            "session_deleted": result.session_deleted,
            "session_error": result.session_error,
        }

    # ------------------------------------------------------------------
    # Webhook
    # ------------------------------------------------------------------
    @app.post("/webhook", dependencies=[Depends(require_auth)])
    def webhook(
        body: TicketBody,
        x_pivot_key: str | None = Header(default=None),
    ) -> Any:
        from .sources.webhook import dispatch_webhook

        try:
            result = dispatch_webhook(engine, body, source_key=x_pivot_key)
        except Exception as exc:  # noqa: BLE001
            logger.exception("webhook dispatch failed")
            return JSONResponse(status_code=500, content={"error": str(exc)})
        status_code = 500 if result.status == "failed" else 200
        return JSONResponse(status_code=status_code, content=_dispatch_payload(result))

    app.include_router(router)
    return app


def _cron_pump(engine: Engine, stop: threading.Event, interval: float) -> None:
    while not stop.is_set():
        try:
            engine.run_cron_tick()
        except Exception:  # noqa: BLE001 - one bad rule must not kill the pump
            logger.exception("pivot cron tick failed")
        if stop.wait(interval):
            return
