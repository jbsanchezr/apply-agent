"""An email provider backed by a directory of ``.eml`` files.

This is the default provider, so the project runs with zero credentials. It
reconstructs threads the way mail clients do: a message belongs to the thread
rooted at the first id in its ``References`` header (falling back to
``In-Reply-To``, then to its own ``Message-ID``).
"""

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from apply_agent.domain import Message
from apply_agent.providers.base import ThreadNotFoundError
from apply_agent.providers.parsing import MalformedEmailError, header_ids, parse_message, read_email


class FakeEmailProvider:
    def __init__(self, directory: Path) -> None:
        if not directory.is_dir():
            raise FileNotFoundError(f"fixture directory does not exist: {directory}")
        self._messages = sorted(
            (_load(path) for path in sorted(directory.glob("*.eml"))),
            key=lambda m: (m.sent_at, m.id),
        )

    def list_messages(self, since: datetime | None = None) -> Sequence[Message]:
        # Fixtures are all inbound, so there is nothing outbound to exclude.
        return [m for m in self._messages if since is None or m.sent_at >= since]

    def get_thread(self, thread_id: str) -> Sequence[Message]:
        thread = [m for m in self._messages if m.thread_id == thread_id]
        if not thread:
            raise ThreadNotFoundError(thread_id)
        return thread


def _load(path: Path) -> Message:
    raw = path.read_bytes()
    headers = read_email(raw)
    own_ids = header_ids(headers["Message-ID"])
    if not own_ids:
        raise MalformedEmailError(f"{path.name}: missing Message-ID")
    root = (header_ids(headers["References"]) or header_ids(headers["In-Reply-To"]) or own_ids)[0]
    return parse_message(raw, message_id=own_ids[0], thread_id=root)
