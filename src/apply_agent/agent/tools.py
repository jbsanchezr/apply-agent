"""The agent's three tools, and nothing else (see D22).

* ``list_new_messages`` is called by the graph itself: deciding what to read
  needs no judgement, and keeping it out of the model's hands keeps each
  sync's cost bounded.
* ``get_thread`` and ``upsert_application`` are offered to the model. Both act
  on the message currently being processed, which the graph supplies, so the
  model cannot read or write anything else.
"""

from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Final

from apply_agent.agent.prompts import render_thread
from apply_agent.domain import Message, MessageAssessment
from apply_agent.providers import EmailProvider
from apply_agent.storage.repository import RecordResult, Repository

# Re-list a few days before the newest recorded message, so mail that arrives
# late or out of order is not missed. Already-processed ids are dropped.
SYNC_OVERLAP: Final = timedelta(days=3)


class Toolbox:
    def __init__(
        self,
        provider: EmailProvider,
        repository: Repository,
        *,
        initial_lookback: timedelta,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._provider = provider
        self._repository = repository
        self._initial_lookback = initial_lookback
        self._clock = clock

    def list_new_messages(self) -> Sequence[Message]:
        """Inbound messages not processed yet, oldest first."""
        latest = self._repository.latest_event_time()
        since = self._clock() - self._initial_lookback if latest is None else latest - SYNC_OVERLAP
        listed = self._provider.list_messages(since=since)
        done = self._repository.processed_message_ids([m.id for m in listed])
        return [m for m in listed if m.id not in done]

    def get_thread(self, current: Message) -> str:
        return render_thread(self._provider.get_thread(current.thread_id), current.id)

    def upsert_application(self, current: Message, assessment: MessageAssessment) -> RecordResult:
        return self._repository.record(current, assessment)
