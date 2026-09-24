"""The LangGraph agent.

    fetch -> next_message -> call_model <-> run_tools
                  ^                              |
                  +------------------------------+   (until the queue is empty)

Each message gets a fresh, short conversation and a step budget. A message
that fails (model error, refusal, no valid result within the budget) is not
recorded, so the next sync retries it; the run continues with the next one.
"""

import logging
import operator
from collections.abc import Sequence
from typing import Annotated, Any, TypedDict

from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.messages.tool import ToolCall
from langchain_core.runnables import Runnable
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import ValidationError

from apply_agent.agent.outcomes import MessageOutcome, Outcome
from apply_agent.agent.prompts import REMINDER, SYSTEM_PROMPT, render_task
from apply_agent.agent.schemas import GET_THREAD, UPSERT_APPLICATION
from apply_agent.agent.tools import Toolbox
from apply_agent.domain import Message, MessageAssessment
from apply_agent.logs import correlated

logger = logging.getLogger(__name__)

ChatModel = Runnable[LanguageModelInput, BaseMessage]


class AgentState(TypedDict, total=False):
    queue: list[Message]
    current: Message | None
    conversation: list[BaseMessage]
    steps: int
    done: bool
    outcomes: Annotated[list[MessageOutcome], operator.add]


def build_graph(
    model: ChatModel, toolbox: Toolbox, *, max_steps: int
) -> CompiledStateGraph[AgentState, None, AgentState, AgentState]:
    def fetch(state: AgentState) -> AgentState:
        queue = list(toolbox.list_new_messages())
        logger.info("new messages listed", extra={"count": len(queue)})
        return {"queue": queue}

    def next_message(state: AgentState) -> AgentState:
        queue = state["queue"]
        if not queue:
            return {"current": None}
        current = queue[0]
        return {
            "queue": queue[1:],
            "current": current,
            "conversation": [SystemMessage(SYSTEM_PROMPT), HumanMessage(render_task(current))],
            "steps": 0,
            "done": False,
        }

    def call_model(state: AgentState) -> AgentState:
        current = _current(state)
        try:
            reply = model.invoke(state["conversation"])
        except Exception as err:  # isolation boundary: one bad call must not end the run
            _log(current).exception("model call failed")
            return _finish(current, Outcome.FAILED, reason=f"model error: {type(err).__name__}")
        if reply.response_metadata.get("stop_reason") == "refusal":
            return _finish(current, Outcome.FAILED, reason="model refused")
        return {"conversation": [*state["conversation"], reply], "steps": state["steps"] + 1}

    def run_tools(state: AgentState) -> AgentState:
        current, conversation = _current(state), state["conversation"]
        last = conversation[-1]
        calls = last.tool_calls if isinstance(last, AIMessage) else []
        replies: list[BaseMessage] = []
        finished: AgentState | None = None
        for call in calls:
            reply, result = _run_tool(toolbox, current, call, already_done=finished is not None)
            replies.append(reply)
            finished = finished or result
        if finished is not None:
            return finished
        if state["steps"] >= max_steps:
            return _finish(current, Outcome.FAILED, reason="no valid result within step budget")
        if not calls:
            replies.append(HumanMessage(REMINDER))
        return {"conversation": [*conversation, *replies]}

    graph = StateGraph(AgentState)
    graph.add_node("fetch", fetch)
    graph.add_node("next_message", next_message)
    graph.add_node("call_model", call_model)
    graph.add_node("run_tools", run_tools)
    graph.add_edge(START, "fetch")
    graph.add_edge("fetch", "next_message")
    graph.add_conditional_edges(
        "next_message", lambda s: "call_model" if s.get("current") else END, ["call_model", END]
    )
    graph.add_conditional_edges(
        "call_model", lambda s: "next_message" if s.get("done") else "run_tools"
    )
    graph.add_conditional_edges(
        "run_tools", lambda s: "next_message" if s.get("done") else "call_model"
    )
    return graph.compile()


def _run_tool(
    toolbox: Toolbox, current: Message, call: ToolCall, *, already_done: bool
) -> tuple[ToolMessage, AgentState | None]:
    call_id = call["id"] or ""
    if already_done:
        return ToolMessage("Ignored: this email is already recorded.", tool_call_id=call_id), None

    if call["name"] == GET_THREAD:
        try:
            thread = toolbox.get_thread(current)
        except Exception:  # a thread that cannot be read should not sink the message
            _log(current).exception("get_thread failed")
            return _error(call_id, "The thread could not be loaded. Assess the email alone."), None
        return ToolMessage(thread, tool_call_id=call_id), None

    if call["name"] == UPSERT_APPLICATION:
        try:
            assessment = MessageAssessment.model_validate(call["args"])
        except ValidationError as err:
            _log(current).info("invalid assessment", extra={"errors": err.error_count()})
            return _error(call_id, f"Nothing was recorded. Fix and retry: {_describe(err)}"), None
        result = toolbox.upsert_application(current, assessment)
        outcome = Outcome.DUPLICATE if result.duplicate else Outcome.RECORDED
        return (
            ToolMessage("Recorded.", tool_call_id=call_id),
            _finish(current, outcome, assessment=assessment, application_id=result.application_id),
        )

    return _error(call_id, f"Unknown tool {call['name']!r}."), None


def _finish(
    current: Message,
    outcome: Outcome,
    *,
    reason: str | None = None,
    assessment: MessageAssessment | None = None,
    application_id: int | None = None,
) -> AgentState:
    result = MessageOutcome(
        message_id=current.id,
        thread_id=current.thread_id,
        outcome=outcome,
        category=assessment.category if assessment else None,
        application_id=application_id,
        reason=reason,
    )
    level = logging.WARNING if outcome is Outcome.FAILED else logging.INFO
    _log(current).log(level, "message processed", extra=result.log_fields())
    return {"done": True, "outcomes": [result]}


def _current(state: AgentState) -> Message:
    current = state.get("current")
    if current is None:  # the graph's edges make this unreachable
        raise RuntimeError("no current message")
    return current


def _log(current: Message) -> logging.LoggerAdapter[logging.Logger]:
    return correlated(logger, thread_id=current.thread_id, message_id=current.id)


def _error(call_id: str, text: str) -> ToolMessage:
    return ToolMessage(text, tool_call_id=call_id, status="error")


def _describe(err: ValidationError) -> str:
    problems: Sequence[Any] = err.errors(include_url=False, include_input=False)
    return "; ".join(f"{'.'.join(map(str, p['loc'])) or 'arguments'}: {p['msg']}" for p in problems)
