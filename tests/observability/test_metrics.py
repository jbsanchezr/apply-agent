"""Prometheus metrics and usage tracking, exercised through the real agent loop."""

import json
import re
from datetime import timedelta
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import Engine

from apply_agent.agent.runner import Agent
from apply_agent.agent.tools import Toolbox
from apply_agent.observability import metrics
from apply_agent.observability.metrics import REGISTRY, ApplicationsCollector
from apply_agent.observability.observer import MetricsObserver
from apply_agent.observability.pricing import cost_usd
from apply_agent.observability.usage import summarise
from apply_agent.storage import make_session_factory
from apply_agent.storage.repository import Repository
from tests.agent.helpers import T0, ListProvider, ScriptedChatModel, get_thread, message, upsert

DASHBOARD = Path(__file__).parents[2] / "deploy" / "grafana" / "dashboards" / "apply-agent.json"


def _value(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def _with_usage(reply: AIMessage, tokens_in: int, tokens_out: int) -> AIMessage:
    reply.usage_metadata = {
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
        "total_tokens": tokens_in + tokens_out,
    }
    return reply


@pytest.fixture
def repository(engine: Engine) -> Repository:
    return Repository(make_session_factory(engine))


@pytest.fixture
def observed_run(repository: Repository) -> tuple[MetricsObserver, dict[str, float]]:
    """Two messages; the second needs a thread read first. Yields metric deltas."""
    names = {
        "recorded": (
            "apply_agent_messages_total",
            {"outcome": "recorded", "category": "rejection"},
        ),
        "steps_sum": ("apply_agent_message_steps_sum", {}),
        "tool_ok": ("apply_agent_tool_calls_total", {"tool": "get_thread", "status": "ok"}),
        "syncs": ("apply_agent_sync_runs_total", {"outcome": "success"}),
    }
    before = {key: _value(n, **labels) for key, (n, labels) in names.items()}
    model = ScriptedChatModel(
        script=[
            _with_usage(upsert(category="rejection"), 900, 100),
            _with_usage(get_thread(), 950, 40),
            _with_usage(upsert(category="rejection", company="Globex"), 1200, 120),
        ]
    )
    toolbox = Toolbox(
        ListProvider([message("m1"), message("m2", "t2", days=1)]),
        repository,
        initial_lookback=timedelta(days=30),
        clock=lambda: T0,
    )
    observer = MetricsObserver()
    Agent(toolbox, model, 4, observer).sync()
    return observer, {k: _value(n, **labels) - before[k] for k, (n, labels) in names.items()}


def test_agent_events_reach_prometheus(
    observed_run: tuple[MetricsObserver, dict[str, float]],
) -> None:
    _, delta = observed_run
    assert delta == {"recorded": 2, "steps_sum": 3, "tool_ok": 1, "syncs": 1}


def test_callbacks_reach_model_calls_made_inside_graph_nodes(
    observed_run: tuple[MetricsObserver, dict[str, float]],
) -> None:
    observer, _ = observed_run
    records = observer.usage.records
    assert [(r.input_tokens, r.output_tokens) for r in records] == [
        (900, 100),
        (950, 40),
        (1200, 120),
    ]
    assert all(r.status == "ok" for r in records)
    labels = {"provider": records[0].provider, "model": records[0].model, "direction": "input"}
    assert _value("apply_agent_llm_tokens_total", **labels) >= 3050


def test_usage_summary(observed_run: tuple[MetricsObserver, dict[str, float]]) -> None:
    observer, _ = observed_run
    summary = summarise(observer.usage.records, use_model_latency=False)
    assert (summary.calls, summary.input_tokens, summary.output_tokens) == (3, 3050, 260)
    assert summary.latency_p50_seconds is not None


@pytest.mark.parametrize(
    ("provider", "model", "expected"),
    [
        ("anthropic", "claude-opus-5", (1_000 * 5 + 200 * 25) / 1e6),
        ("anthropic", "claude-haiku-4-5", (1_000 * 1 + 200 * 5) / 1e6),
        ("ollama", "qwen3:8b", 0.0),
        ("keyword-baseline", "keyword-baseline", 0.0),
        ("anthropic", "claude-unknown-9", None),
        ("someone-else", "model-x", None),
    ],
)
def test_cost(provider: str, model: str, expected: float | None) -> None:
    assert cost_usd(provider, model, 1_000, 200) == (
        pytest.approx(expected) if expected else expected
    )


def test_applications_gauge_is_read_at_scrape_time(repository: Repository) -> None:
    collector = ApplicationsCollector(repository.count_by_status)
    assert next(iter(collector.collect())).samples == []

    from apply_agent.domain import MessageAssessment
    from tests.agent.helpers import assessment

    repository.record(message("m1"), MessageAssessment.model_validate(assessment("offer")))
    [family] = collector.collect()
    assert [(s.labels, s.value) for s in family.samples] == [({"status": "offer_received"}, 1)]


def _exposed_names() -> set[str]:
    families = [*REGISTRY.collect(), *ApplicationsCollector(dict).collect()]
    suffixes = {"counter": ["_total"], "histogram": ["_bucket", "_sum", "_count"], "gauge": [""]}
    return {f.name + s for f in families for s in suffixes.get(f.type, [""])}


def test_every_dashboard_query_uses_a_metric_that_exists() -> None:
    dashboard = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    exprs = [t["expr"] for p in dashboard["panels"] for t in p.get("targets", [])]
    used = {name for expr in exprs for name in re.findall(r"apply_agent_\w+", expr)}
    assert used, "the dashboard has no queries"
    assert used <= _exposed_names(), used - _exposed_names()


def test_dashboard_panels_have_unique_ids_and_a_datasource() -> None:
    panels = json.loads(DASHBOARD.read_text(encoding="utf-8"))["panels"]
    assert len({p["id"] for p in panels}) == len(panels)
    for panel in panels:
        if panel["type"] != "row":
            assert panel["datasource"]["uid"] == "${DS_PROMETHEUS}"


def test_metric_label_values_never_come_from_email_content() -> None:
    labelled = [metrics.MESSAGES, metrics.TOOL_CALLS, metrics.LLM_CALLS, metrics.LLM_TOKENS]
    allowed = {"outcome", "category", "tool", "status", "provider", "model", "direction"}
    for metric in labelled:
        assert set(metric._labelnames) <= allowed
