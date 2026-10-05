"""``EmailProvider`` backed by Gmail, read-only."""

import logging
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from typing import Final

from apply_agent.domain import Message
from apply_agent.providers.base import AlreadyHave
from apply_agent.providers.gmail_api import GmailApi, MessageNotFoundError
from apply_agent.providers.parsing import parse_message

logger = logging.getLogger(__name__)

# Mail the owner wrote, and mail nobody should classify.
BASE_QUERY: Final = "-in:sent -in:drafts -in:spam -in:trash -in:chats"
# Gmail filters on receipt time; the contract is about the Date header. A day of
# overlap covers clock skew and delivery delays. Callers deduplicate by id.
SINCE_MARGIN: Final = timedelta(days=1)
_SENT: Final = "SENT"
_DRAFT: Final = "DRAFT"


class GmailProvider:
    def __init__(self, api: GmailApi, *, max_messages: int = 500) -> None:
        self._api = api
        self._max_messages = max_messages

    def list_messages(
        self, since: datetime | None = None, *, already_have: AlreadyHave | None = None
    ) -> Sequence[Message]:
        query = BASE_QUERY
        if since is not None:
            query += f" after:{int((since - SINCE_MARGIN).timestamp())}"
        ids = self._list_ids(query)
        if already_have is not None:
            # Each fetch is a request against Gmail's per-minute quota (D49);
            # a sync re-lists several days of mail it has already processed.
            known = set(already_have(ids))
            ids = [i for i in ids if i not in known]
        messages = [m for m in self._fetch_all(ids) if not m.outbound]
        return sorted(messages, key=lambda m: (m.sent_at, m.id))

    def get_thread(self, thread_id: str) -> Sequence[Message]:
        # ThreadNotFoundError propagates from the API port.
        ids = self._api.get_thread_message_ids(thread_id)
        return sorted(self._fetch_all(ids), key=lambda m: (m.sent_at, m.id))

    def _list_ids(self, query: str) -> list[str]:
        ids: list[str] = []
        page_token: str | None = None
        while True:
            page, page_token = self._api.list_message_ids(query, page_token)
            ids.extend(page)
            if len(ids) >= self._max_messages:
                logger.warning("message listing truncated at %d", self._max_messages)
                return ids[: self._max_messages]
            if page_token is None:
                return ids

    def _fetch_all(self, ids: Iterable[str]) -> list[Message]:
        messages = []
        for message_id in ids:
            try:
                raw = self._api.get_raw_message(message_id)
            except MessageNotFoundError:
                logger.info("message %s vanished before it could be fetched", message_id)
                continue
            if _DRAFT in raw.label_ids:
                continue
            messages.append(
                parse_message(
                    raw.raw,
                    message_id=raw.id,
                    thread_id=raw.thread_id,
                    outbound=_SENT in raw.label_ids,
                    fallback_sent_at=raw.internal_date,
                )
            )
        return messages
