"""RFC 5322 bytes -> ``Message``.

Both providers go through this one function: the fake reads ``.eml`` files and
Gmail is asked for ``format=raw``, which is the same RFC 5322 bytes. So the
fixtures exercise exactly the parsing path production uses.
"""

import re
from datetime import UTC, datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from typing import Final

from apply_agent.domain import Message
from apply_agent.providers.html_text import html_to_text

MAX_BODY_CHARS: Final = 20_000
"""Bodies are truncated to this length to bound LLM cost on huge marketing mails."""

_ANGLE_ID: Final = re.compile(r"<([^<>\s]+)>")


class MalformedEmailError(ValueError):
    """The bytes cannot be turned into a usable ``Message``."""


def read_email(raw: bytes) -> EmailMessage:
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    if not isinstance(msg, EmailMessage):  # pragma: no cover - guaranteed by policy.default
        raise MalformedEmailError("parser did not return an EmailMessage")
    return msg


def header_ids(value: object) -> list[str]:
    """Message ids from a Message-ID / In-Reply-To / References header, without brackets."""
    return _ANGLE_ID.findall(str(value)) if value is not None else []


def parse_message(
    raw: bytes,
    *,
    message_id: str,
    thread_id: str,
    outbound: bool = False,
    fallback_sent_at: datetime | None = None,
) -> Message:
    """Build a ``Message``. Provider-specific ids and flags are passed in."""
    msg = read_email(raw)
    return Message(
        id=message_id,
        thread_id=thread_id,
        subject=str(msg["Subject"] or ""),
        sender=str(msg["From"] or "") or "(unknown sender)",
        sent_at=_sent_at(msg, fallback_sent_at),
        body_text=_body_text(msg)[:MAX_BODY_CHARS],
        outbound=outbound,
    )


def _sent_at(msg: EmailMessage, fallback: datetime | None) -> datetime:
    raw_date = msg["Date"]
    if raw_date is not None:
        try:
            parsed = parsedate_to_datetime(str(raw_date))
        except (TypeError, ValueError):
            parsed = None
        if parsed is not None:
            # RFC 5322 "-0000" means "UTC, origin unknown"; Python returns it naive.
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    if fallback is not None:
        return fallback
    raise MalformedEmailError("no usable Date header and no fallback timestamp")


def _body_text(msg: EmailMessage) -> str:
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        content = part.get_content()
    except (LookupError, UnicodeDecodeError):
        # Unknown or lying charset: keep what we can rather than drop the message.
        payload = part.get_payload(decode=True)
        content = payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else ""
    if not isinstance(content, str):
        return ""
    return html_to_text(content) if part.get_content_type() == "text/html" else content.strip()
