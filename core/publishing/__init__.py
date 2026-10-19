"""Publishing: one ``publish(content, account)`` interface with retries and idempotency.

Moved from ``omni/``. Existing CLIs (``agents/posting/pinterest_main.py``,
``main.py``) keep their behavior; new callers go through
:class:`core.publishing.publisher.Publisher`.
"""

from core.publishing.models import Account, Content, PublishResult
from core.publishing.publisher import Publisher, TransientPublishError

__all__ = [
    "Account",
    "Content",
    "PublishResult",
    "Publisher",
    "TransientPublishError",
]
