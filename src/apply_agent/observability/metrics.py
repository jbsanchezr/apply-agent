"""Prometheus metrics, registered on a dedicated registry.

A dedicated registry (rather than the process-global default) keeps the
``/metrics`` output to this application's series and lets tests read values
without interference. Label values are bounded: providers, models, tools,
categories and outcomes all come from closed sets or configuration, never
from email content.
"""

from collections.abc import Callable, Iterator, Mapping
from typing import Final

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram
from prometheus_client.core import GaugeMetricFamily, Metric
from prometheus_client.registry import Collector

from apply_agent.agent.outcomes import Outcome
from apply_agent.agent.schemas import KNOWN_TOOLS, UNKNOWN_TOOL
from apply_agent.domain import MessageCategory

REGISTRY: Final = CollectorRegistry(auto_describe=True)

# Local models take tens of seconds per call; hosted ones well under ten.
_LLM_BUCKETS: Final = (0.25, 0.5, 1, 2, 5, 10, 20, 30, 45, 60, 90, 120, 180, 300)
_MESSAGE_BUCKETS: Final = (0.1, 0.5, 1, 2, 5, 10, 20, 30, 60, 120, 300, 600)

SYNC_RUNS = Counter(
    "apply_agent_sync_runs", "Sync runs by outcome.", ["outcome"], registry=REGISTRY
)
SYNC_DURATION = Histogram(
    "apply_agent_sync_duration_seconds",
    "Wall time of a full sync.",
    buckets=(1, 5, 15, 30, 60, 120, 300, 600, 1200, 1800, 3600),
    registry=REGISTRY,
)
LAST_SUCCESSFUL_SYNC = Gauge(
    "apply_agent_last_successful_sync_timestamp_seconds",
    "Unix time at which the last sync finished without error.",
    registry=REGISTRY,
)
MESSAGES = Counter(
    "apply_agent_messages",
    "Processed messages by outcome and predicted category.",
    ["outcome", "category"],
    registry=REGISTRY,
)
MESSAGE_DURATION = Histogram(
    "apply_agent_message_duration_seconds",
    "Wall time to process one message, all agent steps included.",
    buckets=_MESSAGE_BUCKETS,
    registry=REGISTRY,
)
MESSAGE_STEPS = Histogram(
    "apply_agent_message_steps",
    "Model calls needed per message (1 is ideal; higher means retries or thread reads).",
    buckets=(1, 2, 3, 4, 5, 6, 8, 10),
    registry=REGISTRY,
)
TOOL_CALLS = Counter(
    "apply_agent_tool_calls",
    "Tool calls by tool and status.",
    ["tool", "status"],
    registry=REGISTRY,
)
LLM_CALLS = Counter(
    "apply_agent_llm_calls",
    "LLM calls by status (ok, error, refusal).",
    ["provider", "model", "status"],
    registry=REGISTRY,
)
LLM_DURATION = Histogram(
    "apply_agent_llm_call_duration_seconds",
    "Latency of one LLM call.",
    ["provider", "model"],
    buckets=_LLM_BUCKETS,
    registry=REGISTRY,
)
LLM_TOKENS = Counter(
    "apply_agent_llm_tokens",
    "Tokens by direction (input, output; output includes reasoning tokens).",
    ["provider", "model", "direction"],
    registry=REGISTRY,
)
LLM_COST = Counter(
    "apply_agent_llm_cost_usd",
    "Estimated LLM spend in USD.",
    ["provider", "model"],
    registry=REGISTRY,
)
LLM_UNPRICED_CALLS = Counter(
    "apply_agent_llm_unpriced_calls",
    "Calls to a model with no known price (cost not counted, so it is visible here).",
    ["provider", "model"],
    registry=REGISTRY,
)


def _initialise_known_series() -> None:
    """Create every known label combination at zero.

    Prometheus only sees a series once it is scraped. A counter that first
    appears already incremented hides that first increase from ``increase()``
    and ``rate()``, so the first sync's messages would never show on a graph.
    LLM series are created on first use, because their labels come from
    configuration.
    """
    for outcome in ("success", "error"):
        SYNC_RUNS.labels(outcome)
    for message_outcome in Outcome:
        for category in [*(c.value for c in MessageCategory), "none"]:
            MESSAGES.labels(message_outcome.value, category)
    for tool in [*KNOWN_TOOLS, UNKNOWN_TOOL]:
        for status in ("ok", "error"):
            TOOL_CALLS.labels(tool, status)


_initialise_known_series()


class ApplicationsCollector(Collector):
    """``apply_agent_applications{status}``, read from the database at scrape time.

    A scrape-time read keeps the gauge correct across restarts and never
    drifts from the table it describes.
    """

    def __init__(self, count_by_status: Callable[[], Mapping[str, int]]) -> None:
        self._count_by_status = count_by_status

    def collect(self) -> Iterator[Metric]:
        family = GaugeMetricFamily(
            "apply_agent_applications", "Applications by current status.", labels=["status"]
        )
        for status, count in sorted(self._count_by_status().items()):
            family.add_metric([status], count)
        yield family
