"""Chat model construction."""

import json
from typing import Any, Final, assert_never

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
# Deterministic decoding for the local model: the same email gets the same
# answer, which keeps evaluations and recordings reproducible.
OLLAMA_DECODING: Final = {"temperature": 0, "seed": 0}


def model_fingerprint(settings: Settings) -> str:
    """Everything, besides the prompt and tools, that can change the model's answer.

    Recordings are keyed on it (see evaluation.replay). It uses the same
    constants as ``make_chat_model``, so a parameter change invalidates them.
    """
    details: dict[str, Any] = {"llm": settings.llm.value}
    match settings.llm:
        case LlmKind.BASELINE:
            pass
        case LlmKind.OLLAMA:
            details |= {
                "model": settings.ollama_model,
                "num_ctx": settings.ollama_num_ctx,
                **OLLAMA_DECODING,
            }
        case LlmKind.ANTHROPIC:
            details |= {
                "model": settings.anthropic_model,
                "effort": settings.anthropic_effort.value,
                "max_tokens": MAX_TOKENS,
                "refusal_fallback": settings.anthropic_refusal_fallback,
            }
        case _:
            assert_never(settings.llm)
    return json.dumps(details, sort_keys=True)


def make_chat_model(
    settings: Settings, *, cache: BaseCache | None = None
) -> Runnable[LanguageModelInput, BaseMessage]:
    """``cache`` lets the evaluation record and replay responses (see evaluation.replay)."""
    match settings.llm:
        case LlmKind.BASELINE:
            return KeywordBaselineModel().bind_tools(tool_definitions())
        case LlmKind.OLLAMA:
            from langchain_ollama import ChatOllama

            local = ChatOllama(
                model=settings.ollama_model,
                base_url=settings.ollama_base_url,
                num_ctx=settings.ollama_num_ctx,
                temperature=OLLAMA_DECODING["temperature"],
                seed=OLLAMA_DECODING["seed"],
                cache=cache,
            )
            return local.bind_tools(tool_definitions())
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
