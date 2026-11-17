"""Event sources that feed ``Engine.dispatch``.

The HTTP layer calls these instead of branching on ``source`` itself, so the
source key and default-subject rules stay in one place per source.
"""

from .manual import dispatch_manual
from .webhook import dispatch_webhook

__all__ = ["dispatch_manual", "dispatch_webhook"]
