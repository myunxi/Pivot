"""Inbound webhook dispatch.

The request body is untrusted: ``TicketBody`` rejects unknown fields, blank
types, and negative hop counts before anything reaches the store.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping

from ..model import EventSource, TicketBody

if TYPE_CHECKING:
    from ..engine import DispatchResult, Engine


def dispatch_webhook(
    engine: "Engine",
    body: TicketBody | Mapping[str, Any],
    *,
    source_key: str | None = None,
) -> "DispatchResult":
    """Validate an external payload and dispatch it as a ``webhook`` event."""
    validated = (
        body if isinstance(body, TicketBody) else TicketBody.model_validate(body)
    )
    return engine.dispatch(
        validated,
        source=EventSource.WEBHOOK.value,
        source_key=source_key,
    )
