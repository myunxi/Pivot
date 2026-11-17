"""Pivot error hierarchy.

Every error the orchestrator raises on purpose derives from ``PivotError`` so
the HTTP layer can map it to a status without catching bare exceptions.
"""

from __future__ import annotations


class PivotError(Exception):
    """Base class for expected Pivot failures."""


class ConfigError(PivotError, ValueError):
    """Invalid Pivot configuration (env vars, token file, data dir)."""


class PivotClientError(PivotError):
    """The direct daemon rejected a call, or could not be reached.

    ``status_code`` is set when the daemon answered a REST request with a
    non-2xx status; transport and WebSocket failures leave it ``None``.
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class RulesError(PivotError, ValueError):
    """rules.json failed validation; the message is shown to the user."""


class StoreError(PivotError, ValueError):
    """SQLite store failure (unknown row, schema version, unsafe id)."""


class EventNotFoundError(StoreError):
    """No event exists with the requested id."""


class TicketNotFoundError(StoreError):
    """No ticket exists with the requested id."""


class RunNotFoundError(StoreError):
    """No run exists with the requested id."""
