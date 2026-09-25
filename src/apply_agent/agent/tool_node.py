"""Execution of one tool call requested by the model.

Returns plain data (the tool's reply, plus the recorded result for
``upsert_application``). The graph decides what that means for its state.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import ToolMessage
from langchain_core.messages.tool import ToolCall
from pydantic import ValidationError

from apply_agent.agent.schemas import GET_THREAD, UPSERT_APPLICATION
from apply_agent.agent.tools import Toolbox
from apply_agent.domain import Message, MessageAssessment
from apply_agent.logs import correlated
from apply_agent.storage.repository import RecordResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Recorded:
    assessment: MessageAssessment
    result: RecordResult


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    reply: ToolMessage
    ok: bool
    recorded: Recorded | None = None


def run_tool(toolbox: Toolbox, current: Message, call: ToolCall) -> ToolOutcome:
    call_id = call["id"] or ""
    log = correlated(logger, thread_id=current.thread_id, message_id=current.id)

    if call["name"] == GET_THREAD:
        try:
            thread = toolbox.get_thread(current)
        except Exception:  # a thread that cannot be read should not sink the message
            log.exception("get_thread failed")
            return _error(call_id, "The thread could not be loaded. Assess the email alone.")
        return ToolOutcome(ToolMessage(thread, tool_call_id=call_id), ok=True)

    if call["name"] == UPSERT_APPLICATION:
        try:
            assessment = MessageAssessment.model_validate(call["args"])
        except ValidationError as err:
            log.info("invalid assessment", extra={"errors": err.error_count()})
            return _error(call_id, f"Nothing was recorded. Fix and retry: {_describe(err)}")
        result = toolbox.upsert_application(current, assessment)
        reply = ToolMessage("Recorded.", tool_call_id=call_id)
        return ToolOutcome(reply, ok=True, recorded=Recorded(assessment, result))

    return _error(call_id, f"Unknown tool {call['name']!r}.")


def ignored(call: ToolCall) -> ToolMessage:
    """Reply to a call made after the email was already recorded."""
    return ToolMessage("Ignored: this email is already recorded.", tool_call_id=call["id"] or "")


def _error(call_id: str, text: str) -> ToolOutcome:
    return ToolOutcome(ToolMessage(text, tool_call_id=call_id, status="error"), ok=False)


def _describe(err: ValidationError) -> str:
    problems: Sequence[Any] = err.errors(include_url=False, include_input=False)
    return "; ".join(f"{'.'.join(map(str, p['loc'])) or 'arguments'}: {p['msg']}" for p in problems)
