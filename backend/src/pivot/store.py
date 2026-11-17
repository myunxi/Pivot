"""SQLite persistence for Pivot events, tickets, runs, and audit entries.

One process (``pivot serve``) owns one connection guarded by a re-entrant
lock: the cron pump runs in a thread while the HTTP API serves requests. WAL
mode lets a second reader (``pivot cron`` scripts are expected to use their
own short-lived store) see committed rows without blocking writes.

The event table's ``UNIQUE(source, source_key)`` is the idempotency contract:
dispatching the same manual key or the same cron tick twice returns the
original event instead of creating another ticket.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from .errors import (
    EventNotFoundError,
    RunNotFoundError,
    StoreError,
    TicketNotFoundError,
)
from .model import (
    ACTIVE_TICKET_STATES,
    AuditEntry,
    StoredEvent,
    StoredRun,
    StoredTicket,
    TicketState,
)

SCHEMA_VERSION = 1
MAX_TICKET_LIMIT = 1000
DEFAULT_TICKET_LIMIT = 30
_SAFE_TICKET_ID = re.compile(r"[A-Za-z0-9._-]+\Z")
_UNSET: Any = object()


def _now() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


def _non_empty(value: object, field: str) -> str:
    if not isinstance(value, str) or value.strip() == "":
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _state_value(value: TicketState | str) -> str:
    raw = value.value if isinstance(value, TicketState) else value
    allowed = {state.value for state in TicketState}
    if not isinstance(raw, str) or raw not in allowed:
        raise ValueError(f"unknown ticket state: {raw!r}")
    return raw


def _validate_ticket_id(ticket_id: str) -> str:
    if (
        not isinstance(ticket_id, str)
        or _SAFE_TICKET_ID.fullmatch(ticket_id) is None
        or ".." in ticket_id
    ):
        raise ValueError(f"invalid ticket id: {ticket_id!r}")
    return ticket_id


class PivotStore:
    """Thread-safe SQLite store. Close it when the owning process exits."""

    def __init__(self, path: Path | str, *, data_dir: Path | None = None):
        self._memory = isinstance(path, str) and path == ":memory:"
        self.path = ":memory:" if self._memory else str(path)
        if data_dir is not None:
            self.data_dir: Path | None = Path(data_dir)
        elif self._memory:
            self.data_dir = None
        else:
            self.data_dir = Path(path).parent

        self._lock = RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA busy_timeout = 5000")
        if not self._memory:
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self._create_schema()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _create_schema(self) -> None:
        with self._lock, self._conn:
            version = int(self._conn.execute("PRAGMA user_version").fetchone()[0])
            if version not in (0, SCHEMA_VERSION):
                raise StoreError(
                    f"unsupported pivot store version {version} "
                    f"(this build supports {SCHEMA_VERSION})"
                )
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    source_key TEXT NOT NULL,
                    type TEXT NOT NULL,
                    subject TEXT,
                    summary TEXT,
                    detail TEXT,
                    link TEXT,
                    hops INTEGER NOT NULL DEFAULT 0,
                    payload TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE (source, source_key)
                );

                CREATE TABLE IF NOT EXISTS tickets (
                    id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL REFERENCES events(id),
                    state TEXT NOT NULL,
                    rule TEXT NOT NULL,
                    title TEXT NOT NULL,
                    cwd TEXT NOT NULL,
                    agent TEXT NOT NULL,
                    worktree_path TEXT,
                    agent_instance_id TEXT,
                    needs_human_since TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS tickets_created
                    ON tickets (created_at DESC);
                CREATE INDEX IF NOT EXISTS tickets_state
                    ON tickets (state);

                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    ticket_id TEXT NOT NULL REFERENCES tickets(id),
                    attempt INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT
                );

                CREATE INDEX IF NOT EXISTS runs_ticket
                    ON runs (ticket_id);

                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_id TEXT,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    detail TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS audit_ticket
                    ON audit (ticket_id);

                CREATE TABLE IF NOT EXISTS cron_state (
                    rule TEXT PRIMARY KEY,
                    last_fired_at TEXT NOT NULL
                );
                """
            )
            self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------
    def add_event(
        self,
        *,
        source: str,
        source_key: str,
        event_type: str,
        subject: str | None = None,
        summary: str | None = None,
        detail: str | None = None,
        link: str | None = None,
        hops: int = 0,
        payload: dict[str, Any] | None = None,
    ) -> tuple[StoredEvent, bool]:
        """Insert an event, or return the existing row for the same key.

        Returns ``(event, created)``; ``created`` is False on a duplicate so
        the dispatch API can answer ``duplicate`` without spawning anything.
        """
        source = _non_empty(source, "source")
        source_key = _non_empty(source_key, "source_key")
        event_type = _non_empty(event_type, "event_type")
        if isinstance(hops, bool) or not isinstance(hops, int) or hops < 0:
            raise ValueError("hops must be a non-negative integer")
        payload_json = (
            json.dumps(payload, ensure_ascii=False, sort_keys=True)
            if payload is not None
            else None
        )
        with self._lock, self._conn:
            existing = self._conn.execute(
                "SELECT * FROM events WHERE source = ? AND source_key = ?",
                (source, source_key),
            ).fetchone()
            if existing is not None:
                return _event_from_row(existing), False
            self._conn.execute(
                """
                INSERT INTO events (
                    id, source, source_key, type, subject, summary, detail,
                    link, hops, payload, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"evt-{uuid4().hex}",
                    source,
                    source_key,
                    event_type,
                    subject,
                    summary,
                    detail,
                    link,
                    hops,
                    payload_json,
                    _now(),
                ),
            )
            row = self._conn.execute(
                "SELECT * FROM events WHERE source = ? AND source_key = ?",
                (source, source_key),
            ).fetchone()
        if row is None:  # pragma: no cover - insert just succeeded
            raise StoreError("event insert vanished")
        return _event_from_row(row), True

    def get_event(self, event_id: str) -> StoredEvent | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM events WHERE id = ?", (event_id,)
            ).fetchone()
        return None if row is None else _event_from_row(row)

    # ------------------------------------------------------------------
    # Tickets
    # ------------------------------------------------------------------
    def create_ticket(
        self,
        *,
        event_id: str,
        rule: str,
        title: str,
        cwd: str,
        agent: str,
        worktree_path: str | None = None,
        state: TicketState | str = TicketState.QUEUED,
    ) -> StoredTicket:
        event_id = _non_empty(event_id, "event_id")
        rule = _non_empty(rule, "rule")
        title = _non_empty(title, "title")
        cwd = _non_empty(cwd, "cwd")
        agent = _non_empty(agent, "agent")
        state_value = _state_value(state)
        ticket_id = f"tkt-{uuid4().hex}"
        now = _now()
        with self._lock, self._conn:
            event = self._conn.execute(
                "SELECT id FROM events WHERE id = ?", (event_id,)
            ).fetchone()
            if event is None:
                raise EventNotFoundError(f"unknown event: {event_id}")
            self._conn.execute(
                """
                INSERT INTO tickets (
                    id, event_id, state, rule, title, cwd, agent,
                    worktree_path, agent_instance_id, needs_human_since,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)
                """,
                (
                    ticket_id,
                    event_id,
                    state_value,
                    rule,
                    title,
                    cwd,
                    agent,
                    worktree_path,
                    now,
                    now,
                ),
            )
            row = self._conn.execute(
                "SELECT * FROM tickets WHERE id = ?", (ticket_id,)
            ).fetchone()
        if row is None:  # pragma: no cover
            raise StoreError("ticket insert vanished")
        return _ticket_from_row(row)

    def get_ticket(self, ticket_id: str) -> StoredTicket | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tickets WHERE id = ?", (ticket_id,)
            ).fetchone()
        return None if row is None else _ticket_from_row(row)

    def require_ticket(self, ticket_id: str) -> StoredTicket:
        ticket = self.get_ticket(ticket_id)
        if ticket is None:
            raise TicketNotFoundError(f"unknown ticket: {ticket_id}")
        return ticket

    def update_ticket(
        self,
        ticket_id: str,
        *,
        state: TicketState | str | None = None,
        title: str | None | Any = _UNSET,
        worktree_path: str | None | Any = _UNSET,
        agent_instance_id: str | None | Any = _UNSET,
        needs_human_since: str | None | Any = _UNSET,
    ) -> StoredTicket:
        """Patch the fields that are not ``_UNSET``; clear with an explicit None."""
        assignments: dict[str, Any] = {}
        if state is not None:
            assignments["state"] = _state_value(state)
        if title is not _UNSET:
            assignments["title"] = _non_empty(title, "title")
        if worktree_path is not _UNSET:
            assignments["worktree_path"] = worktree_path
        if agent_instance_id is not _UNSET:
            assignments["agent_instance_id"] = agent_instance_id
        if needs_human_since is not _UNSET:
            assignments["needs_human_since"] = needs_human_since
        assignments["updated_at"] = _now()

        columns = ", ".join(f"{column} = ?" for column in assignments)
        values = [*assignments.values(), ticket_id]
        with self._lock, self._conn:
            cursor = self._conn.execute(
                f"UPDATE tickets SET {columns} WHERE id = ?", values
            )
            if cursor.rowcount == 0:
                raise TicketNotFoundError(f"unknown ticket: {ticket_id}")
            row = self._conn.execute(
                "SELECT * FROM tickets WHERE id = ?", (ticket_id,)
            ).fetchone()
        if row is None:  # pragma: no cover
            raise TicketNotFoundError(f"unknown ticket: {ticket_id}")
        return _ticket_from_row(row)

    def list_tickets(
        self,
        *,
        state: TicketState | str | None = None,
        limit: int = DEFAULT_TICKET_LIMIT,
    ) -> list[StoredTicket]:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        capped = min(limit, MAX_TICKET_LIMIT)
        query = "SELECT * FROM tickets"
        params: list[Any] = []
        if state is not None:
            query += " WHERE state = ?"
            params.append(_state_value(state))
        query += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
        params.append(capped)
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [_ticket_from_row(row) for row in rows]

    def active_ticket_count(self) -> int:
        """Tickets in spawning/running/needs_human — used by concurrency limits."""
        placeholders = ", ".join("?" for _ in ACTIVE_TICKET_STATES)
        with self._lock:
            row = self._conn.execute(
                f"SELECT COUNT(*) FROM tickets WHERE state IN ({placeholders})",
                tuple(ACTIVE_TICKET_STATES),
            ).fetchone()
        return int(row[0])

    # ------------------------------------------------------------------
    # Runs
    # ------------------------------------------------------------------
    def add_run(
        self,
        ticket_id: str,
        *,
        status: str = TicketState.SPAWNING.value,
        attempt: int = 1,
        error: str | None = None,
    ) -> StoredRun:
        status = _non_empty(status, "status")
        if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
            raise ValueError("attempt must be a positive integer")
        run_id = f"run-{uuid4().hex}"
        with self._lock, self._conn:
            ticket = self._conn.execute(
                "SELECT id FROM tickets WHERE id = ?", (ticket_id,)
            ).fetchone()
            if ticket is None:
                raise TicketNotFoundError(f"unknown ticket: {ticket_id}")
            self._conn.execute(
                """
                INSERT INTO runs (
                    id, ticket_id, attempt, status, error, started_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL)
                """,
                (run_id, ticket_id, attempt, status, error, _now()),
            )
            row = self._conn.execute(
                "SELECT * FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
        if row is None:  # pragma: no cover
            raise StoreError("run insert vanished")
        return _run_from_row(row)

    def finish_run(
        self, run_id: str, *, status: str, error: str | None = None
    ) -> StoredRun:
        status = _non_empty(status, "status")
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE runs SET status = ?, error = ?, finished_at = ? WHERE id = ?",
                (status, error, _now(), run_id),
            )
            if cursor.rowcount == 0:
                raise RunNotFoundError(f"unknown run: {run_id}")
            row = self._conn.execute(
                "SELECT * FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
        if row is None:  # pragma: no cover
            raise RunNotFoundError(f"unknown run: {run_id}")
        return _run_from_row(row)

    def list_runs(self, ticket_id: str) -> list[StoredRun]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM runs WHERE ticket_id = ? "
                "ORDER BY started_at ASC, rowid ASC",
                (ticket_id,),
            ).fetchall()
        return [_run_from_row(row) for row in rows]

    # ------------------------------------------------------------------
    # Audit
    # ------------------------------------------------------------------
    def audit(
        self,
        *,
        actor: str,
        action: str,
        ticket_id: str | None = None,
        detail: str | None = None,
    ) -> AuditEntry:
        actor = _non_empty(actor, "actor")
        action = _non_empty(action, "action")
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO audit (ticket_id, actor, action, detail, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (ticket_id, actor, action, detail, _now()),
            )
            row = self._conn.execute(
                "SELECT * FROM audit WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
        if row is None:  # pragma: no cover
            raise StoreError("audit insert vanished")
        return _audit_from_row(row)

    def list_audit(
        self, ticket_id: str | None = None, *, limit: int = 100
    ) -> list[AuditEntry]:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        query = "SELECT * FROM audit"
        params: list[Any] = []
        if ticket_id is not None:
            query += " WHERE ticket_id = ?"
            params.append(ticket_id)
        query += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(min(limit, 1000))
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [_audit_from_row(row) for row in rows]

    # ------------------------------------------------------------------
    # Cron bookkeeping
    # ------------------------------------------------------------------
    def get_cron_state(self, rule: str) -> str | None:
        """Return the last trigger this rule consumed, or None before baseline."""
        rule = _non_empty(rule, "rule")
        with self._lock:
            row = self._conn.execute(
                "SELECT last_fired_at FROM cron_state WHERE rule = ?", (rule,)
            ).fetchone()
        return None if row is None else str(row["last_fired_at"])

    def set_cron_state(self, rule: str, last_fired_at: str) -> None:
        """Record a consumed trigger. Called only after its event is stored."""
        rule = _non_empty(rule, "rule")
        last_fired_at = _non_empty(last_fired_at, "last_fired_at")
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO cron_state (rule, last_fired_at) VALUES (?, ?)
                ON CONFLICT (rule) DO UPDATE SET last_fired_at = excluded.last_fired_at
                """,
                (rule, last_fired_at),
            )

    # ------------------------------------------------------------------
    # Deletion and work dirs
    # ------------------------------------------------------------------
    def delete_ticket(self, ticket_id: str) -> bool:
        """Delete the ticket, its event, runs, and ticket-scoped audit rows.

        Worktree directories are deliberately left alone; the engine only
        removes the input directory (``remove_work_dir``).
        """
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT event_id FROM tickets WHERE id = ?", (ticket_id,)
            ).fetchone()
            if row is None:
                return False
            event_id = row["event_id"]
            self._conn.execute("DELETE FROM runs WHERE ticket_id = ?", (ticket_id,))
            self._conn.execute("DELETE FROM audit WHERE ticket_id = ?", (ticket_id,))
            self._conn.execute("DELETE FROM tickets WHERE id = ?", (ticket_id,))
            self._conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
        return True

    def work_dir(self, ticket_id: str) -> Path:
        ticket_id = _validate_ticket_id(ticket_id)
        if self.data_dir is None:
            raise StoreError("store has no data directory for work files")
        return self.data_dir / "work" / ticket_id

    def write_input(self, ticket_id: str, content: str) -> Path:
        target = self.work_dir(ticket_id)
        target.mkdir(parents=True, exist_ok=True)
        os.chmod(target, 0o700)
        path = target / "input.md"
        path.write_text(content, encoding="utf-8")
        os.chmod(path, 0o600)
        return path

    def remove_work_dir(self, ticket_id: str) -> None:
        if self.data_dir is None:
            return
        target = self.work_dir(ticket_id)
        root = self.data_dir / "work"
        if not target.is_relative_to(root):
            raise StoreError(f"refusing to remove outside {root}: {target}")
        shutil.rmtree(target, ignore_errors=True)


def _event_from_row(row: sqlite3.Row) -> StoredEvent:
    payload: dict[str, Any] | None = None
    if row["payload"]:
        try:
            decoded = json.loads(row["payload"])
        except json.JSONDecodeError as exc:  # pragma: no cover - internal writes
            raise StoreError(f"corrupt event payload for {row['id']}") from exc
        payload = decoded if isinstance(decoded, dict) else None
    return StoredEvent(
        id=row["id"],
        source=row["source"],
        source_key=row["source_key"],
        event_type=row["type"],
        subject=row["subject"],
        summary=row["summary"],
        detail=row["detail"],
        link=row["link"],
        hops=row["hops"],
        payload=payload,
        created_at=row["created_at"],
    )


def _ticket_from_row(row: sqlite3.Row) -> StoredTicket:
    return StoredTicket(
        id=row["id"],
        event_id=row["event_id"],
        state=row["state"],
        rule=row["rule"],
        title=row["title"],
        cwd=row["cwd"],
        agent=row["agent"],
        worktree_path=row["worktree_path"],
        agent_instance_id=row["agent_instance_id"],
        needs_human_since=row["needs_human_since"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _run_from_row(row: sqlite3.Row) -> StoredRun:
    return StoredRun(
        id=row["id"],
        ticket_id=row["ticket_id"],
        attempt=row["attempt"],
        status=row["status"],
        error=row["error"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
    )


def _audit_from_row(row: sqlite3.Row) -> AuditEntry:
    return AuditEntry(
        id=row["id"],
        ticket_id=row["ticket_id"],
        actor=row["actor"],
        action=row["action"],
        detail=row["detail"],
        created_at=row["created_at"],
    )
