"""Test doubles for the agent: a scripted chat model and an in-memory provider."""

from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field, SkipValidation

from apply_agent.agent.schemas import GET_THREAD, UPSERT_APPLICATION
from apply_agent.domain import Message
from apply_agent.providers import ThreadNotFoundError
from apply_agent.providers.base import AlreadyHave

T0 = datetime(2026, 7, 1, 9, 0, tzinfo=UTC)

Step = AIMessage | Exception | Callable[[list[BaseMessage]], AIMessage]


class ScriptedChatModel(BaseChatModel):
    """Replies with the next scripted step; records every conversation it is shown."""

    # SkipValidation: pydantic would otherwise try to coerce callables into AIMessage.
    script: SkipValidation[list[Step]] = Field(default_factory=list)
    calls: list[list[BaseMessage]] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.calls.append(list(messages))
        if not self.script:
            raise AssertionError("the model was called more often than scripted")
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        reply = step if isinstance(step, AIMessage) else step(messages)
        return ChatResult(generations=[ChatGeneration(message=reply)])


def assessment(
    category: str = "other",
    company: str | None = "Acme",
    role: str | None = "Backend Engineer",
    *,
    is_job: bool = True,
    summary: str = "Something happened",
) -> dict[str, Any]:
    return {
        "is_job_application": is_job,
        "category": category,
        "company": company,
        "role": role,
        "summary": summary,
    }


def upsert(**kwargs: Any) -> AIMessage:
    call = {
        "name": UPSERT_APPLICATION,
        "args": assessment(**kwargs),
        "id": "c1",
        "type": "tool_call",
    }
    return AIMessage("", tool_calls=[call])


def tool_call(name: str, args: dict[str, Any] | None = None) -> AIMessage:
    return AIMessage(
        "", tool_calls=[{"name": name, "args": args or {}, "id": "c0", "type": "tool_call"}]
    )


def get_thread() -> AIMessage:
    return tool_call(GET_THREAD)


def message(
    message_id: str,
    thread_id: str = "t1",
    *,
    days: int = 0,
    subject: str = "Your application",
    body: str = "Hello",
    outbound: bool = False,
) -> Message:
    return Message(
        id=message_id,
        thread_id=thread_id,
        subject=subject,
        sender="Recruiter <r@acme.example>",
        sent_at=T0 + timedelta(days=days),
        body_text=body,
        outbound=outbound,
    )


class ListProvider:
    """``EmailProvider`` over a fixed list; records the ``since`` it was asked for."""

    def __init__(self, messages: Sequence[Message]) -> None:
        self.messages = list(messages)
        self.since_calls: list[datetime | None] = []

    def list_messages(
        self, since: datetime | None = None, *, already_have: AlreadyHave | None = None
    ) -> Sequence[Message]:
        # Ignores the hint on purpose: callers must not rely on it.
        self.since_calls.append(since)
        return sorted(
            (m for m in self.messages if not m.outbound and (since is None or m.sent_at >= since)),
            key=lambda m: m.sent_at,
        )

    def get_thread(self, thread_id: str) -> Sequence[Message]:
        thread = sorted(
            (m for m in self.messages if m.thread_id == thread_id), key=lambda m: m.sent_at
        )
        if not thread:
            raise ThreadNotFoundError(thread_id)
        return thread
