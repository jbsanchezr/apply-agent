from collections.abc import Callable, Sequence
from datetime import timedelta

import pytest
from sqlalchemy import Engine

from apply_agent.agent.runner import Agent
from apply_agent.agent.tools import Toolbox
from apply_agent.domain import Message
from apply_agent.storage import make_session_factory
from apply_agent.storage.repository import Repository
from tests.agent.helpers import T0, ListProvider, ScriptedChatModel, Step

AgentFactory = Callable[..., tuple[Agent, ScriptedChatModel]]


@pytest.fixture
def repository(engine: Engine) -> Repository:
    return Repository(make_session_factory(engine))


@pytest.fixture
def make_agent(repository: Repository) -> AgentFactory:
    """Build an agent over the given messages, driven by a scripted model."""

    def factory(
        messages: Sequence[Message], script: Sequence[Step], *, max_steps: int = 4
    ) -> tuple[Agent, ScriptedChatModel]:
        model = ScriptedChatModel(script=list(script))
        toolbox = Toolbox(
            ListProvider(messages),
            repository,
            initial_lookback=timedelta(days=30),
            clock=lambda: T0 + timedelta(days=1),
        )
        return Agent(toolbox, model, max_steps), model

    return factory
