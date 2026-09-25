"""The LangGraph agent.

    fetch -> next_message -> call_model <-> run_tools
                  ^                              |
                  +------------------------------+   (until the queue is empty)

Each message gets a fresh, short conversation and a step budget. A message
that fails (model error, refusal, no valid result within the budget) is not
recorded, so the next sync retries it; the run continues with the next one.
Progress is reported to an ``Observer`` (metrics, tracing), never logged
with email content.
"""

import logging
import operator
from typing import Annotated, TypedDict

from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import Runnable
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from apply_agent.agent.outcomes import MessageOutcome, Outcome
from apply_agent.agent.prompts import REMINDER, SYSTEM_PROMPT, render_task
from apply_agent.agent.tool_node import Recorded, ignored, run_tool
from apply_agent.agent.tools import Toolbox
from apply_agent.domain import Message
from apply_agent.logs import correlated
from apply_agent.observability.observer import NullObserver, Observer

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
    model: ChatModel,
    toolbox: Toolbox,
    *,
    max_steps: int,
    observer: Observer | None = None,
) -> CompiledStateGraph[AgentState, None, AgentState, AgentState]:
    obs: Observer = observer or NullObserver()

    def finish(
        current: Message,
        outcome: Outcome,
        steps: int,
        *,
        reason: str | None = None,
        recorded: Recorded | None = None,
    ) -> AgentState:
        result = MessageOutcome(
            message_id=current.id,
            thread_id=current.thread_id,
            outcome=outcome,
            category=recorded.assessment.category if recorded else None,
            application_id=recorded.result.application_id if recorded else None,
            reason=reason,
            steps=steps,
        )
        level = logging.WARNING if outcome is Outcome.FAILED else logging.INFO
        _log(current).log(level, "message processed", extra=result.log_fields())
        obs.message_finished(result)
        return {"done": True, "outcomes": [result]}

    def fetch(state: AgentState) -> AgentState:
        queue = list(toolbox.list_new_messages())
        logger.info("new messages listed", extra={"count": len(queue)})
        return {"queue": queue}

    def next_message(state: AgentState) -> AgentState:
        queue = state["queue"]
        if not queue:
            return {"current": None}
        current = queue[0]
        obs.message_started(current)
        return {
            "queue": queue[1:],
            "current": current,
            "conversation": [SystemMessage(SYSTEM_PROMPT), HumanMessage(render_task(current))],
            "steps": 0,
            "done": False,
        }

    def call_model(state: AgentState) -> AgentState:
        current, steps = _current(state), state["steps"] + 1
        try:
            reply = model.invoke(state["conversation"])
        except Exception as err:  # isolation boundary: one bad call must not end the run
            _log(current).exception("model call failed")
            return finish(
                current, Outcome.FAILED, steps, reason=f"model error: {type(err).__name__}"
            )
        if reply.response_metadata.get("stop_reason") == "refusal":
            return finish(current, Outcome.FAILED, steps, reason="model refused")
        return {"conversation": [*state["conversation"], reply], "steps": steps}

    def run_tools(state: AgentState) -> AgentState:
        current, conversation, steps = _current(state), state["conversation"], state["steps"]
        last = conversation[-1]
        calls = last.tool_calls if isinstance(last, AIMessage) else []
        replies: list[BaseMessage] = []
        recorded: Recorded | None = None
        for call in calls:
            if recorded is not None:
                replies.append(ignored(call))
                continue
            outcome = run_tool(toolbox, current, call)
            obs.tool_called(call["name"], ok=outcome.ok)
            replies.append(outcome.reply)
            recorded = outcome.recorded
        if recorded is not None:
            kind = Outcome.DUPLICATE if recorded.result.duplicate else Outcome.RECORDED
            return finish(current, kind, steps, recorded=recorded)
        if steps >= max_steps:
            return finish(
                current, Outcome.FAILED, steps, reason="no valid result within step budget"
            )
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


def _current(state: AgentState) -> Message:
    current = state.get("current")
    if current is None:  # the graph's edges make this unreachable
        raise RuntimeError("no current message")
    return current


def _log(current: Message) -> logging.LoggerAdapter[logging.Logger]:
    return correlated(logger, thread_id=current.thread_id, message_id=current.id)
