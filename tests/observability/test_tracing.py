"""Langfuse tracing, captured in memory: no network, no credentials."""

from datetime import timedelta
from uuid import uuid4

import pytest
from langfuse import Langfuse
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import Engine

from apply_agent.agent.runner import Agent
from apply_agent.agent.tools import Toolbox
from apply_agent.observability.tracing import (
    LangfuseObserver,
    make_langfuse_observer,
    redact_email_bodies,
)
from apply_agent.storage import make_session_factory
from apply_agent.storage.repository import Repository
from tests.agent.helpers import T0, ListProvider, ScriptedChatModel, get_thread, message, upsert

PRIVATE_BODY = "The confidential salary figure is 98,765 EUR."


def _traced_run(engine: Engine, *, redact: bool) -> list[ReadableSpan]:
    exporter = InMemorySpanExporter()
    # The SDK keeps one client per public key; a unique key gives each run its exporter.
    client = Langfuse(
        public_key=f"pk-lf-test-{uuid4().hex}",
        secret_key="sk-lf-test",  # noqa: S106 - fake credential for an in-memory exporter
        base_url="http://127.0.0.1:9",
        span_exporter=exporter,
    )
    toolbox = Toolbox(
        ListProvider([message("m1", body=PRIVATE_BODY)]),
        Repository(make_session_factory(engine)),
        initial_lookback=timedelta(days=30),
        clock=lambda: T0,
    )
    model = ScriptedChatModel(script=[get_thread(), upsert(category="offer")])
    Agent(toolbox, model, 4, LangfuseObserver(client, redact_bodies=redact)).sync()
    client.flush()
    return list(exporter.get_finished_spans())


@pytest.fixture
def spans(engine: Engine) -> list[ReadableSpan]:
    return _traced_run(engine, redact=True)


def _attributes_text(spans: list[ReadableSpan]) -> str:
    return " ".join(str(value) for span in spans for value in (span.attributes or {}).values())


def test_trace_has_sync_message_generation_and_tool_spans(spans: list[ReadableSpan]) -> None:
    names = sorted(span.name for span in spans)
    assert names == sorted(
        ["sync", "assess_message", "llm", "llm", "get_thread", "upsert_application"]
    )


def _parent_id(span: ReadableSpan) -> int:
    assert span.parent is not None, f"{span.name} has no parent"
    return span.parent.span_id


def _own_id(span: ReadableSpan) -> int:
    context = span.get_span_context()
    assert context is not None
    return context.span_id


def test_spans_nest_under_the_message_and_the_sync(spans: list[ReadableSpan]) -> None:
    by_name = {span.name: span for span in spans}
    assert _parent_id(by_name["assess_message"]) == _own_id(by_name["sync"])
    message_id = _own_id(by_name["assess_message"])
    for span in spans:
        if span.name in {"llm", "get_thread", "upsert_application"}:
            assert _parent_id(span) == message_id


def test_email_bodies_are_redacted_by_default(spans: list[ReadableSpan]) -> None:
    text = _attributes_text(spans)
    assert "98,765" not in text
    assert "characters redacted" in text
    assert "m1" in text, "correlation ids must still be there"


def test_bodies_are_sent_when_redaction_is_off(engine: Engine) -> None:
    assert "98,765" in _attributes_text(_traced_run(engine, redact=False))


def test_redaction_keeps_headers_and_replaces_the_body() -> None:
    rendered = (
        '<email direction="received">\nFrom: R <r@acme.example>\nDate: 2026-07-01\n'
        "Subject: Offer\n\nSecret body\nsecond line\n</email>"
    )
    redacted = redact_email_bodies(f"Assess this.\n\n{rendered}")
    assert "Subject: Offer" in redacted
    assert "Secret body" not in redacted
    assert "[23 characters redacted]" in redacted


def test_enabled_tracing_without_credentials_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError, match="LANGFUSE_PUBLIC_KEY"):
        make_langfuse_observer(redact_bodies=True)
