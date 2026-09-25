"""Composition root: wire settings into a runnable sync."""

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Final

from apply_agent.agent.graph import AgentState, ChatModel, build_graph
from apply_agent.agent.llm import make_chat_model
from apply_agent.agent.outcomes import SyncReport
from apply_agent.agent.tools import Toolbox
from apply_agent.config import Settings
from apply_agent.observability.observer import (
    CompositeObserver,
    MetricsObserver,
    NullObserver,
    Observer,
)
from apply_agent.providers import EmailProvider, make_provider
from apply_agent.storage import init_db, make_engine, make_session_factory
from apply_agent.storage.repository import Repository

logger = logging.getLogger(__name__)

# LangGraph counts every node execution against a recursion limit. Our loop is
# bounded by the queue length and the per-message step budget, so the limit
# is derived from those and only acts as a backstop.
_NODES_PER_STEP: Final = 2
_SLACK: Final = 10


@dataclass(frozen=True, slots=True)
class Agent:
    toolbox: Toolbox
    model: ChatModel
    max_steps: int
    observer: Observer = field(default_factory=NullObserver)

    def sync(self, *, max_messages: int = 500) -> SyncReport:
        graph = build_graph(
            self.model, self.toolbox, max_steps=self.max_steps, observer=self.observer
        )
        limit = max_messages * (self.max_steps * _NODES_PER_STEP + 1) + _SLACK
        initial: AgentState = {"outcomes": []}
        self.observer.sync_started()
        try:
            final = graph.invoke(
                initial,
                config={"recursion_limit": limit, "callbacks": self.observer.callbacks()},
            )
        except Exception as err:
            self.observer.sync_finished(None, err)
            raise
        report = SyncReport.from_outcomes(final.get("outcomes", []))
        self.observer.sync_finished(report, None)
        logger.info(
            "sync finished",
            extra={
                "processed": report.processed,
                "recorded": report.recorded,
                "failed": report.failed,
            },
        )
        return report


def make_observer(settings: Settings) -> Observer:
    observers: list[Observer] = [MetricsObserver()]
    if settings.langfuse_enabled:
        # Imported lazily: the Langfuse SDK is only loaded when tracing is on.
        from apply_agent.observability.tracing import make_langfuse_observer

        observers.append(make_langfuse_observer(redact_bodies=settings.langfuse_redact_bodies))
    return CompositeObserver(observers)


def build_agent(
    settings: Settings,
    *,
    provider: EmailProvider | None = None,
    model: ChatModel | None = None,
) -> tuple[Agent, Repository]:
    engine = make_engine(settings.database_url)
    init_db(engine)
    repository = Repository(make_session_factory(engine))
    toolbox = Toolbox(
        provider or make_provider(settings),
        repository,
        initial_lookback=timedelta(days=settings.initial_lookback_days),
    )
    agent = Agent(
        toolbox,
        model or make_chat_model(settings),
        settings.max_agent_steps,
        make_observer(settings),
    )
    return agent, repository
