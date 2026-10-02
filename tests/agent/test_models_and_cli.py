"""Model construction, the keyword baseline, logging and the CLI, end to end."""

import json
import logging
from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from apply_agent.__main__ import main
from apply_agent.agent.baseline import assess_text
from apply_agent.agent.llm import REFUSAL_FALLBACK_BETA, make_chat_model
from apply_agent.agent.prompts import render_task
from apply_agent.config import Settings
from apply_agent.domain import MessageAssessment, MessageCategory
from apply_agent.logs import JsonFormatter, correlated
from apply_agent.providers import FakeEmailProvider
from tests.conftest import FIXTURES_DIR

FIXTURES = FakeEmailProvider(FIXTURES_DIR / "emails").list_messages()


def _anthropic_payload(monkeypatch: pytest.MonkeyPatch, **env: str) -> dict[str, object]:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    model = make_chat_model(Settings.from_env({"APPLY_AGENT_LLM": "anthropic", **env}))
    bound, kwargs = model.bound, model.kwargs  # type: ignore[attr-defined]
    payload: dict[str, object] = bound._get_request_payload(
        [SystemMessage("system"), HumanMessage("email")], **kwargs
    )
    return payload


def test_anthropic_request_is_configured_as_intended(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _anthropic_payload(monkeypatch)
    assert payload["model"] == "claude-opus-5"
    assert payload["output_config"] == {"effort": "low"}
    assert payload["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    assert [t["name"] for t in payload["tools"]] == ["get_thread", "upsert_application"]  # type: ignore[attr-defined]
    assert payload["betas"] == [REFUSAL_FALLBACK_BETA]
    assert payload["fallbacks"] == "default"


def test_refusal_fallback_can_be_switched_off(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _anthropic_payload(monkeypatch, APPLY_AGENT_ANTHROPIC_REFUSAL_FALLBACK="false")
    assert "betas" not in payload
    assert "fallbacks" not in payload


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda m: m.id)
def test_baseline_output_always_satisfies_the_contract(fixture: object) -> None:
    MessageAssessment.model_validate(assess_text(render_task(fixture)))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("message_id", "expected"),
    [
        ("ats-0019283@halcyonhealth.example", MessageCategory.REJECTION),
        ("q-20260713-44@quillondata.example", MessageCategory.REJECTION),
        ("k-6102@kestrel-robotics.example", MessageCategory.OFFER),
        ("vb-docs-3310@veridianbank.example", MessageCategory.INFORMATION_REQUEST),
    ],
)
def test_baseline_gets_the_obvious_cases_right(message_id: str, expected: MessageCategory) -> None:
    fixture = next(m for m in FIXTURES if m.id == message_id)
    assert assess_text(render_task(fixture))["category"] == expected.value


def test_json_logs_carry_the_correlation_ids() -> None:
    record = logging.makeLogRecord({"msg": "processed", "levelname": "INFO", "name": "t"})
    adapter = correlated(logging.getLogger("t"), thread_id="thr-1", message_id="m-1")
    _, kwargs = adapter.process("processed", {"extra": {"outcome": "recorded"}})
    for key, value in kwargs["extra"].items():
        setattr(record, key, value)

    payload = json.loads(JsonFormatter().format(record))

    assert payload["thread_id"] == "thr-1"
    assert payload["message_id"] == "m-1"
    assert payload["outcome"] == "recorded"


def test_cli_sync_runs_on_fixtures_and_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("APPLY_AGENT_DATABASE_URL", f"sqlite:///{tmp_path / 'cli.db'}")
    monkeypatch.setenv("APPLY_AGENT_FIXTURES_DIR", str(FIXTURES_DIR / "emails"))
    monkeypatch.setenv("APPLY_AGENT_INITIAL_LOOKBACK_DAYS", "3650")

    assert main(["sync"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert main(["sync"]) == 0
    second = json.loads(capsys.readouterr().out)

    assert first["processed"] == len(FIXTURES) == first["recorded"]
    assert second["processed"] == 0


def test_ollama_model_is_local_deterministic_and_has_room_for_the_prompt() -> None:
    model = make_chat_model(Settings.from_env({"APPLY_AGENT_LLM": "ollama"}))
    bound, kwargs = model.bound, model.kwargs  # type: ignore[attr-defined]
    assert bound.model == "qwen3:8b"
    assert bound.base_url == "http://localhost:11434"
    assert (bound.temperature, bound.seed, bound.num_ctx) == (0, 0, 8_192)
    assert bound.num_predict == 4_096
    assert [t["function"]["name"] for t in kwargs["tools"]] == ["get_thread", "upsert_application"]
