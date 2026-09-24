"""An in-memory ``GmailApi`` serving the ``.eml`` fixtures in Gmail's shape.

Beyond the fixtures, each thread that starts with one of the candidate's own
(sent) messages gets that message added, and one draft is added, because
real Gmail threads contain both and the provider must handle them.
"""

import re
from datetime import UTC, datetime, timedelta
from email import policy
from email.message import EmailMessage
from pathlib import Path

from apply_agent.providers import ThreadNotFoundError
from apply_agent.providers.gmail_api import MessageNotFoundError, RawGmailMessage
from apply_agent.providers.parsing import header_ids, read_email

DELIVERY_DELAY = timedelta(minutes=3)
_AFTER = re.compile(r"after:(\d+)")


def _owner_message(subject: str, sent_at: datetime, message_id: str) -> bytes:
    msg = EmailMessage(policy=policy.default)
    msg["From"] = "Sam Rivera <sam.rivera@example.com>"
    msg["To"] = "careers@brightloom.example"
    msg["Subject"] = subject
    msg["Date"] = sent_at
    msg["Message-ID"] = f"<{message_id}>"
    msg.set_content("Hello, please find my application attached.\n")
    return msg.as_bytes()


class FixtureGmailApi:
    def __init__(self, emails_dir: Path, *, page_size: int = 10) -> None:
        self.page_size = page_size
        self.vanished: set[str] = set()
        self.queries: list[str] = []
        self._messages: dict[str, RawGmailMessage] = {}
        thread_ids: dict[str, str] = {}

        for index, path in enumerate(sorted(emails_dir.glob("*.eml"))):
            raw = path.read_bytes()
            headers = read_email(raw)
            refs = header_ids(headers["References"]) or header_ids(headers["Message-ID"])
            thread = thread_ids.setdefault(refs[0], f"thr-{len(thread_ids):03d}")
            received = headers["Date"].datetime.astimezone(UTC) + DELIVERY_DELAY
            self._add(f"msg-{index:03d}", thread, raw, set(), received)

        # The candidate's original application, opening the Brightloom thread.
        brightloom = thread_ids["sent-3f9a1c@mail.example.com"]
        sent_at = datetime(2026, 6, 30, 20, 0, tzinfo=UTC)
        subject = "Application - Machine Learning Engineer"
        self._add(
            "msg-sent",
            brightloom,
            _owner_message(subject, sent_at, "sent-3f9a1c@mail.example.com"),
            {"SENT"},
            sent_at,
        )
        self._add(
            "msg-draft",
            brightloom,
            _owner_message("Re: follow-up", sent_at, "draft-1@mail.example.com"),
            {"DRAFT"},
            sent_at,
        )

    def _add(self, gid: str, thread: str, raw: bytes, labels: set[str], at: datetime) -> None:
        self._messages[gid] = RawGmailMessage(gid, thread, raw, frozenset(labels), at)

    def list_message_ids(self, query: str, page_token: str | None) -> tuple[list[str], str | None]:
        self.queries.append(query)
        after = _AFTER.search(query)
        matching = [
            m.id
            for m in self._messages.values()
            if not ("-in:sent" in query and "SENT" in m.label_ids)
            and not ("-in:drafts" in query and "DRAFT" in m.label_ids)
            and (after is None or m.internal_date.timestamp() > int(after.group(1)))
        ]
        start = int(page_token or 0)
        end = start + self.page_size
        return matching[start:end], (str(end) if end < len(matching) else None)

    def get_raw_message(self, message_id: str) -> RawGmailMessage:
        if message_id in self.vanished or message_id not in self._messages:
            raise MessageNotFoundError(message_id)
        return self._messages[message_id]

    def get_thread_message_ids(self, thread_id: str) -> list[str]:
        ids = [m.id for m in self._messages.values() if m.thread_id == thread_id]
        if not ids:
            raise ThreadNotFoundError(thread_id)
        return ids
