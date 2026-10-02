"""A narrow, typed port over the Gmail REST API.

``googleapiclient`` is untyped and exposes every Gmail endpoint, including
send, delete and modify. This module is the only place that touches it, and
the only calls it makes are three GETs. Everything else in the codebase depends
on the ``GmailApi`` protocol, which has no way to express a write.
"""

import base64
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, Protocol, Self

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from apply_agent.providers.base import ThreadNotFoundError

logger = logging.getLogger(__name__)

GMAIL_READONLY_SCOPE: Final = "https://www.googleapis.com/auth/gmail.readonly"
_USER: Final = "me"
_HTTP_FORBIDDEN: Final = 403
_HTTP_NOT_FOUND: Final = 404
_HTTP_TOO_MANY_REQUESTS: Final = 429
# Gmail enforces a per-user quota per minute and reports it as 403 or 429. The
# client's own retries give up within seconds; the quota window is a minute.
QUOTA_BACKOFF_SECONDS: Final = (5.0, 15.0, 30.0, 60.0, 60.0)
_RATE_LIMIT_MARKERS: Final = ("rateLimitExceeded", "RATE_LIMIT_EXCEEDED", "Quota exceeded")


class MessageNotFoundError(LookupError):
    """The message disappeared (e.g. deleted by the user) between listing and fetching."""


@dataclass(frozen=True, slots=True)
class RawGmailMessage:
    id: str
    thread_id: str
    raw: bytes
    label_ids: frozenset[str]
    internal_date: datetime


class GmailApi(Protocol):
    def list_message_ids(self, query: str, page_token: str | None) -> tuple[list[str], str | None]:
        """One page of message ids matching a Gmail search query, plus the next page token."""
        ...

    def get_raw_message(self, message_id: str) -> RawGmailMessage: ...

    def get_thread_message_ids(self, thread_id: str) -> list[str]: ...


class GoogleGmailApi:
    """``GmailApi`` implemented with the official client. Transient errors are retried."""

    def __init__(
        self,
        service: Any,
        *,
        num_retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._messages = service.users().messages()
        self._threads = service.users().threads()
        self._num_retries = num_retries
        self._sleep = sleep

    @classmethod
    def from_credentials(cls, credentials: Credentials) -> Self:
        service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
        return cls(service)

    def list_message_ids(self, query: str, page_token: str | None) -> tuple[list[str], str | None]:
        request = self._messages.list(userId=_USER, q=query, pageToken=page_token)
        response: dict[str, Any] = self._execute(request)
        ids = [str(m["id"]) for m in response.get("messages", [])]
        next_token = response.get("nextPageToken")
        return ids, str(next_token) if next_token else None

    def get_raw_message(self, message_id: str) -> RawGmailMessage:
        request = self._messages.get(userId=_USER, id=message_id, format="raw")
        try:
            data: dict[str, Any] = self._execute(request)
        except HttpError as err:
            if err.resp.status == _HTTP_NOT_FOUND:
                raise MessageNotFoundError(message_id) from err
            raise
        return RawGmailMessage(
            id=str(data["id"]),
            thread_id=str(data["threadId"]),
            raw=_b64url_decode(str(data["raw"])),
            label_ids=frozenset(data.get("labelIds", [])),
            internal_date=datetime.fromtimestamp(int(data["internalDate"]) / 1000, tz=UTC),
        )

    def get_thread_message_ids(self, thread_id: str) -> list[str]:
        request = self._threads.get(userId=_USER, id=thread_id, format="minimal")
        try:
            data: dict[str, Any] = self._execute(request)
        except HttpError as err:
            if err.resp.status == _HTTP_NOT_FOUND:
                raise ThreadNotFoundError(thread_id) from err
            raise
        return [str(m["id"]) for m in data.get("messages", [])]

    def _execute(self, request: Any) -> Any:
        """Run a request, waiting out per-user quota errors instead of failing the sync."""
        for delay in (*QUOTA_BACKOFF_SECONDS, None):
            try:
                return request.execute(num_retries=self._num_retries)
            except HttpError as err:
                if delay is None or not _is_rate_limited(err):
                    raise
                logger.warning("gmail quota exceeded, retrying", extra={"delay_s": delay})
                self._sleep(delay)
        raise AssertionError("unreachable")


def _is_rate_limited(err: HttpError) -> bool:
    if err.resp.status == _HTTP_TOO_MANY_REQUESTS:
        return True
    content = err.content.decode("utf-8", "replace") if isinstance(err.content, bytes) else ""
    return err.resp.status == _HTTP_FORBIDDEN and any(m in content for m in _RATE_LIMIT_MARKERS)


def _b64url_decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
