"""Langfuse tracing, built on the core SDK (no LangChain integration package).

Trace shape per sync:

    sync (agent)
      assess_message (chain)   one per email; thread_id/message_id in metadata
        llm (generation)       each model call: model, usage, cost, input, output
        get_thread / upsert_application (tool)

Privacy: with ``redact_bodies`` (the default), email bodies are replaced by
their length before anything leaves the process. Sender and subject lines are
kept so traces stay debuggable. Tracing sends prompts to the Langfuse server,
so for a real inbox point it at a self-hosted instance (see D38).
"""

import os
import re
from typing import Any, Final
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import LLMResult
from langfuse import Langfuse

from apply_agent.agent.outcomes import MessageOutcome, Outcome, SyncReport
from apply_agent.domain import Message
from apply_agent.observability.observer import NullObserver
from apply_agent.observability.pricing import cost_usd
from apply_agent.observability.usage import first_ai_message, identify_model, token_counts

REQUIRED_ENV: Final = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")
_BODY: Final = re.compile(r"(<email[^>]*>\n(?:.*\n)*?Subject: .*\n\n)(.*?)(\n</email>)", re.DOTALL)


def redact_email_bodies(text: str) -> str:
    return _BODY.sub(lambda m: f"{m[1]}[{len(m[2])} characters redacted]{m[3]}", text)


class LangfuseObserver(NullObserver):
    def __init__(self, client: Langfuse, *, redact_bodies: bool = True) -> None:
        self._client = client
        self._redact = redact_bodies
        self._sync: Any = None
        self._message: Any = None
        self._generations: dict[UUID, tuple[Any, str, str]] = {}
        self._callback = _GenerationCallback(self)

    # -- agent events ---------------------------------------------------------
    def sync_started(self) -> None:
        self._sync = self._client.start_observation(name="sync", as_type="agent")

    def sync_finished(self, report: SyncReport | None, error: BaseException | None) -> None:
        if self._sync is None:
            return
        summary = None
        if report is not None:
            summary = {
                "processed": report.processed,
                "recorded": report.recorded,
                "failed": report.failed,
                "by_category": report.by_category,
            }
        self._sync.update(
            output=summary,
            level="ERROR" if error else None,
            status_message=repr(error) if error else None,
        )
        self._sync.end()
        self._sync = None
        self._client.flush()

    def message_started(self, message: Message) -> None:
        self._message = self._parent().start_observation(
            name="assess_message",
            as_type="chain",
            metadata={"thread_id": message.thread_id, "message_id": message.id},
        )

    def message_finished(self, outcome: MessageOutcome) -> None:
        if self._message is None:
            return
        self._message.update(
            output={
                "outcome": outcome.outcome.value,
                "category": outcome.category.value if outcome.category else None,
                "application_id": outcome.application_id,
                "steps": outcome.steps,
                "reason": outcome.reason,
            },
            level="WARNING" if outcome.outcome is Outcome.FAILED else None,
        )
        self._message.end()
        self._message = None

    def tool_called(self, tool: str, *, ok: bool) -> None:
        span = self._parent().start_observation(
            name=tool, as_type="tool", level=None if ok else "ERROR"
        )
        span.end()

    def callbacks(self) -> list[BaseCallbackHandler]:
        return [self._callback]

    # -- model calls, driven by the LangChain callback --------------------------
    def start_generation(
        self, run_id: UUID, provider: str, model: str, messages: list[BaseMessage]
    ) -> None:
        generation = self._parent().start_observation(
            name="llm",
            as_type="generation",
            model=model,
            input=[{"role": m.type, "content": self._content(m)} for m in messages],
            metadata={"provider": provider},
        )
        self._generations[run_id] = (generation, provider, model)

    def end_generation(self, run_id: UUID, reply: AIMessage | None) -> None:
        entry = self._generations.pop(run_id, None)
        if entry is None:
            return
        generation, provider, model = entry
        tokens_in, tokens_out = token_counts(reply)
        cost = cost_usd(provider, model, tokens_in, tokens_out)
        generation.update(
            output={"content": reply.content, "tool_calls": reply.tool_calls} if reply else None,
            usage_details={"input": tokens_in, "output": tokens_out},
            cost_details={"total": cost} if cost is not None else None,
        )
        generation.end()

    def fail_generation(self, run_id: UUID, error: BaseException) -> None:
        entry = self._generations.pop(run_id, None)
        if entry is not None:
            entry[0].update(level="ERROR", status_message=repr(error))
            entry[0].end()

    def _parent(self) -> Any:
        return self._message or self._sync or self._client

    def _content(self, message: BaseMessage) -> str:
        content = str(message.content)
        return redact_email_bodies(content) if self._redact else content


class _GenerationCallback(BaseCallbackHandler):
    def __init__(self, owner: LangfuseObserver) -> None:
        self._owner = owner

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[BaseMessage]],
        *,
        run_id: UUID,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        provider, model = identify_model(metadata, kwargs)
        self._owner.start_generation(run_id, provider, model, messages[0] if messages else [])

    def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        self._owner.end_generation(run_id, first_ai_message(response))

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._owner.fail_generation(run_id, error)


def make_langfuse_observer(*, redact_bodies: bool) -> LangfuseObserver:
    """Fail fast on missing credentials instead of silently tracing nothing."""
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        raise RuntimeError(f"Langfuse tracing is enabled but {', '.join(missing)} is not set")
    return LangfuseObserver(Langfuse(), redact_bodies=redact_bodies)
