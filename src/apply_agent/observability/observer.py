"""The events the agent reports, and the observers that consume them.

The graph and runner know only the ``Observer`` interface. Metrics and
tracing are implementations, so either can be switched off (or replaced)
without touching agent code. Messages are processed one at a time, so an
observer can track "the current message" without any locking.
"""

import time
from collections.abc import Sequence
from typing import Protocol

from langchain_core.callbacks import BaseCallbackHandler

from apply_agent.agent.outcomes import MessageOutcome, SyncReport
from apply_agent.domain import Message
from apply_agent.observability import metrics
from apply_agent.observability.usage import UsageTracker


class Observer(Protocol):
    def sync_started(self) -> None: ...
    def sync_finished(self, report: SyncReport | None, error: BaseException | None) -> None: ...
    def message_started(self, message: Message) -> None: ...
    def message_finished(self, outcome: MessageOutcome) -> None: ...
    def tool_called(self, tool: str, *, ok: bool) -> None: ...
    def callbacks(self) -> list[BaseCallbackHandler]:
        """LangChain callbacks to attach to model calls."""
        ...


class NullObserver:
    def sync_started(self) -> None:
        pass

    def sync_finished(self, report: SyncReport | None, error: BaseException | None) -> None:
        pass

    def message_started(self, message: Message) -> None:
        pass

    def message_finished(self, outcome: MessageOutcome) -> None:
        pass

    def tool_called(self, tool: str, *, ok: bool) -> None:
        pass

    def callbacks(self) -> list[BaseCallbackHandler]:
        return []


class MetricsObserver(NullObserver):
    """Feeds the Prometheus metrics in ``observability.metrics``."""

    def __init__(self) -> None:
        self.usage = UsageTracker()
        self._sync_t0 = 0.0
        self._message_t0 = 0.0

    def sync_started(self) -> None:
        self._sync_t0 = time.perf_counter()

    def sync_finished(self, report: SyncReport | None, error: BaseException | None) -> None:
        metrics.SYNC_DURATION.observe(time.perf_counter() - self._sync_t0)
        metrics.SYNC_RUNS.labels("error" if error else "success").inc()
        if error is None:
            metrics.LAST_SUCCESSFUL_SYNC.set_to_current_time()

    def message_started(self, message: Message) -> None:
        self._message_t0 = time.perf_counter()

    def message_finished(self, outcome: MessageOutcome) -> None:
        category = outcome.category.value if outcome.category else "none"
        metrics.MESSAGES.labels(outcome.outcome.value, category).inc()
        metrics.MESSAGE_DURATION.observe(time.perf_counter() - self._message_t0)
        metrics.MESSAGE_STEPS.observe(outcome.steps)

    def tool_called(self, tool: str, *, ok: bool) -> None:
        metrics.TOOL_CALLS.labels(tool, "ok" if ok else "error").inc()

    def callbacks(self) -> list[BaseCallbackHandler]:
        return [self.usage]


class CompositeObserver:
    def __init__(self, observers: Sequence[Observer]) -> None:
        self._observers = list(observers)

    def sync_started(self) -> None:
        for o in self._observers:
            o.sync_started()

    def sync_finished(self, report: SyncReport | None, error: BaseException | None) -> None:
        for o in self._observers:
            o.sync_finished(report, error)

    def message_started(self, message: Message) -> None:
        for o in self._observers:
            o.message_started(message)

    def message_finished(self, outcome: MessageOutcome) -> None:
        for o in self._observers:
            o.message_finished(outcome)

    def tool_called(self, tool: str, *, ok: bool) -> None:
        for o in self._observers:
            o.tool_called(tool, ok=ok)

    def callbacks(self) -> list[BaseCallbackHandler]:
        return [cb for o in self._observers for cb in o.callbacks()]
