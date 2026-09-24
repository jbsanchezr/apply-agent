"""The email source abstraction.

The interface is read-only by construction: it has no method that could send,
delete, label or otherwise modify mail, so no implementation can be asked to.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from apply_agent.domain import Message


class ThreadNotFoundError(LookupError):
    """Raised when a thread id is unknown to the provider."""

    def __init__(self, thread_id: str) -> None:
        super().__init__(f"thread not found: {thread_id}")
        self.thread_id = thread_id


class EmailProvider(Protocol):
    def list_messages(self, since: datetime | None = None) -> Sequence[Message]:
        """Inbound messages, oldest first.

        Every inbound message sent at or after ``since`` is included. A provider
        may also return some older ones (its server-side filter can be coarser),
        so callers deduplicate by ``Message.id``. Outbound messages are excluded.
        """
        ...

    def get_thread(self, thread_id: str) -> Sequence[Message]:
        """All messages in a thread, oldest first, including outbound ones.

        Raises ``ThreadNotFoundError`` for an unknown thread id.
        """
        ...
