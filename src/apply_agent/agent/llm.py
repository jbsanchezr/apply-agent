"""Chat model construction."""

from typing import Final, assert_never

from langchain_core.caches import BaseCache
from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import BaseMessage
from langchain_core.runnables import Runnable

from apply_agent.agent.baseline import KeywordBaselineModel
from apply_agent.agent.schemas import tool_definitions
from apply_agent.config import LlmKind, Settings

# Classification output is small; the headroom is for adaptive thinking.
MAX_TOKENS: Final = 4_096
REQUEST_TIMEOUT_SECONDS: Final = 120.0
# Server-side refusal fallback: if the model declines, the API re-runs the
# request on a fallback model it chooses by refusal category.
REFUSAL_FALLBACK_BETA: Final = "server-side-fallback-2026-07-01"


def make_chat_model(
    settings: Settings, *, cache: BaseCache | None = None
) -> Runnable[LanguageModelInput, BaseMessage]:
    """``cache`` lets the evaluation record and replay responses (see evaluation.replay)."""
    match settings.llm:
        case LlmKind.BASELINE:
            return KeywordBaselineModel().bind_tools(tool_definitions())
        case LlmKind.ANTHROPIC:
            # Imported lazily so the default path never needs the Anthropic SDK configured.
            from langchain_anthropic import ChatAnthropic

            fallback = settings.anthropic_refusal_fallback
            model = ChatAnthropic(
                model=settings.anthropic_model,
                max_tokens=MAX_TOKENS,
                reasoning_effort=settings.anthropic_effort.value,
                default_request_timeout=REQUEST_TIMEOUT_SECONDS,
                max_retries=3,
                betas=[REFUSAL_FALLBACK_BETA] if fallback else None,
                model_kwargs={"fallbacks": "default"} if fallback else {},
                cache=cache,
            )
            # One tool call per turn keeps the loop simple: think, act, observe.
            return model.bind_tools(tool_definitions(), parallel_tool_calls=False)
        case _:
            assert_never(settings.llm)
