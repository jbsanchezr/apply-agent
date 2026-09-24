"""A deterministic keyword classifier that speaks the tool-calling protocol.

It is the default "model", so the project runs end to end with no API key,
and it is the baseline the real LLM is measured against in the evaluation.
It is deliberately naive: it reads single keywords, never calls get_thread,
and takes the company from the sender's domain. Its mistakes on the tricky
fixtures are the point.
"""

import re
from collections.abc import Callable, Sequence
from email.utils import parseaddr
from typing import Any, Final

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool

from apply_agent.agent.schemas import UPSERT_APPLICATION
from apply_agent.domain import MessageCategory


def _any_of(*phrases: str) -> re.Pattern[str]:
    return re.compile("|".join(phrases), re.IGNORECASE)


# First match wins, so the order encodes precedence (an offer outranks a request).
_RULES: Final[Sequence[tuple[MessageCategory, re.Pattern[str]]]] = [
    (
        MessageCategory.OFFER,
        _any_of(
            r"offer you the position",
            r"extend(?:ing)? (?:you )?an? (?:conditional )?offer",
            r"offer letter",
            r"offer details",
        ),
    ),
    (
        MessageCategory.REJECTION,
        _any_of(
            r"unfortunately",
            r"regret to inform",
            r"not (?:be )?moving forward",
            r"won't be (?:progressing|continuing)",
            r"not been selected",
            r"decided to (?:move forward|continue|pursue) with (?:a|other|another)",
            r"lamentablemente",
        ),
    ),
    (MessageCategory.INTERVIEW_INVITATION, _any_of(r"interview", r"assessment", r"next stage")),
    (
        MessageCategory.INFORMATION_REQUEST,
        _any_of(
            r"could you (?:please )?(?:send|share|let me know)",
            r"please (?:complete|answer|upload)",
            r"screening questions",
        ),
    ),
]
_JOB_HINT: Final = re.compile(r"\b(?:application|applying|applied)\b", re.I)
_ROLE: Final = re.compile(
    r"\b(?:for|of) (?:the )?(?P<role>[A-Z][\w&,/()' -]{2,60}?) (?:position|role)\b"
)
_FIELD: Final = re.compile(r"^(From|Subject): (.*)$", re.M)
_BODY: Final = re.compile(r"^Subject: .*?\n\n(.*?)\n</email>", re.S | re.M)
_GENERIC_LABELS: Final = frozenset(
    {"example", "test", "com", "org", "net", "io", "co", "uk", "de", "es", "mail", "careers"}
)


def assess_text(task: str) -> dict[str, Any]:
    """Tool arguments for ``upsert_application``, from a rendered email."""
    fields = dict(_FIELD.findall(task))
    subject = fields.get("Subject", "").strip()
    body_match = _BODY.search(task)
    text = f"{subject}\n{body_match.group(1) if body_match else ''}"

    category = next((c for c, rule in _RULES if rule.search(text)), MessageCategory.OTHER)
    is_job = category is not MessageCategory.OTHER or bool(_JOB_HINT.search(text))
    role = _ROLE.search(text)
    return {
        "is_job_application": is_job,
        "category": category.value,
        "company": _company_from_sender(fields.get("From", "")) if is_job else None,
        "role": role.group("role").strip() if role and is_job else None,
        "summary": f"{category.value.replace('_', ' ').capitalize()}: {subject}"[:200],
    }


def _company_from_sender(sender: str) -> str:
    domain = parseaddr(sender)[1].rpartition("@")[2]
    labels = [label for label in domain.lower().split(".") if label not in _GENERIC_LABELS]
    return labels[-1].replace("-", " ").title() if labels else "Unknown"


class KeywordBaselineModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "keyword-baseline"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        task = next(m for m in messages if isinstance(m, HumanMessage))
        call = {
            "name": UPSERT_APPLICATION,
            "args": assess_text(str(task.content)),
            "id": "baseline-call",
            "type": "tool_call",
        }
        return ChatResult(generations=[ChatGeneration(message=AIMessage("", tool_calls=[call]))])

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        # The baseline knows its one tool already; binding is a no-op.
        return self
