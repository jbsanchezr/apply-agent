"""A LangChain callback that measures every LLM call.

It sees calls regardless of provider, and it records each one twice: into
Prometheus (cumulative, for dashboards) and into ``records`` (for the
evaluation's per-run summary).
"""

import statistics
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from apply_agent.observability import metrics
from apply_agent.observability.pricing import cost_usd

_NANOSECONDS: Final = 1e9


@dataclass(frozen=True, slots=True)
class CallRecord:
    provider: str
    model: str
    status: str
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    wall_seconds: float
    # Latency reported by the model server itself (Ollama does). Unlike wall
    # time it survives replay from a recording.
    model_seconds: float | None


@dataclass(frozen=True, slots=True)
class UsageSummary:
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float | None  # None if any call used an unpriced model
    latency_p50_seconds: float | None
    latency_p95_seconds: float | None


def summarise(records: Sequence[CallRecord], *, use_model_latency: bool) -> UsageSummary:
    costs = [r.cost_usd for r in records]
    latencies: list[float] = sorted(
        r.model_seconds if use_model_latency and r.model_seconds is not None else r.wall_seconds
        for r in records
        if not use_model_latency or r.model_seconds is not None
    )
    return UsageSummary(
        calls=len(records),
        input_tokens=sum(r.input_tokens for r in records),
        output_tokens=sum(r.output_tokens for r in records),
        cost_usd=None if None in costs else sum(c for c in costs if c is not None),
        latency_p50_seconds=statistics.median(latencies) if latencies else None,
        latency_p95_seconds=(
            statistics.quantiles(latencies, n=20, method="inclusive")[-1]
            if len(latencies) >= 2
            else (latencies[0] if latencies else None)
        ),
    )


def token_counts(reply: AIMessage | None) -> tuple[int, int]:
    usage = reply.usage_metadata if reply else None
    return (usage["input_tokens"], usage["output_tokens"]) if usage else (0, 0)


def identify_model(metadata: dict[str, Any] | None, kwargs: dict[str, Any]) -> tuple[str, str]:
    meta = metadata or {}
    llm_type = str(kwargs.get("invocation_params", {}).get("_type", "unknown"))
    provider = str(meta.get("ls_provider", llm_type))
    model = str(meta.get("ls_model_name", llm_type))
    return provider, model


class UsageTracker(BaseCallbackHandler):
    def __init__(self) -> None:
        self.records: list[CallRecord] = []
        self._started: dict[UUID, tuple[str, str, float]] = {}

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
        self._started[run_id] = (provider, model, time.perf_counter())

    def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        started = self._started.pop(run_id, None)
        if started is None:
            return
        provider, model, t0 = started
        reply = first_ai_message(response)
        meta = reply.response_metadata if reply else {}
        tokens_in, tokens_out = token_counts(reply)
        status = "refusal" if meta.get("stop_reason") == "refusal" else "ok"
        server_ns = meta.get("total_duration")
        record = CallRecord(
            provider=provider,
            model=model,
            status=status,
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            cost_usd=cost_usd(provider, model, tokens_in, tokens_out),
            wall_seconds=time.perf_counter() - t0,
            model_seconds=server_ns / _NANOSECONDS if isinstance(server_ns, int) else None,
        )
        self.records.append(record)
        _export(record)

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        started = self._started.pop(run_id, None)
        if started is not None:
            provider, model, _ = started
            metrics.LLM_CALLS.labels(provider, model, "error").inc()


def first_ai_message(response: LLMResult) -> AIMessage | None:
    for generations in response.generations:
        for generation in generations:
            if isinstance(generation, ChatGeneration) and isinstance(generation.message, AIMessage):
                return generation.message
    return None


def _export(record: CallRecord) -> None:
    labels = (record.provider, record.model)
    metrics.LLM_CALLS.labels(*labels, record.status).inc()
    metrics.LLM_DURATION.labels(*labels).observe(record.wall_seconds)
    metrics.LLM_TOKENS.labels(*labels, "input").inc(record.input_tokens)
    metrics.LLM_TOKENS.labels(*labels, "output").inc(record.output_tokens)
    if record.cost_usd is None:
        metrics.LLM_UNPRICED_CALLS.labels(*labels).inc()
    else:
        metrics.LLM_COST.labels(*labels).inc(record.cost_usd)
