"""Manual dispatch (the dashboard form)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping

from ..model import EventSource, TicketBody

if TYPE_CHECKING:
    from ..engine import DispatchResult, Engine


def dispatch_manual(
    engine: "Engine",
    body: TicketBody | Mapping[str, Any],
    *,
    source_key: str | None = None,
) -> "DispatchResult":
    """Validate a form payload and dispatch it as a ``manual`` event."""
    validated = (
        body if isinstance(body, TicketBody) else TicketBody.model_validate(body)
    )
    return engine.dispatch(
        validated,
        source=EventSource.MANUAL.value,
        source_key=source_key,
    )
