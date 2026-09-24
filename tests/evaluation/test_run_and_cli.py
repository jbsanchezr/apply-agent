import json
from pathlib import Path

import pytest

from apply_agent.agent.baseline import KeywordBaselineModel
from apply_agent.evaluation.cli import (
    EXIT_BELOW_THRESHOLD,
    EXIT_MODEL_UNAVAILABLE,
    EXIT_STALE_RECORDING,
    main,
)
from apply_agent.evaluation.dataset import load_dataset
from apply_agent.evaluation.metrics import FAILED
from apply_agent.evaluation.report import format_report
from apply_agent.evaluation.run import evaluate
from tests.agent.helpers import ScriptedChatModel, upsert
from tests.conftest import FIXTURES_DIR


def test_every_label_maps_to_a_fixture_message() -> None:
    dataset = load_dataset(FIXTURES_DIR)
    assert len(dataset) == len(list((FIXTURES_DIR / "emails").glob("*.eml")))
    assert len({item.message_id for item in dataset}) == len(dataset)


def test_baseline_scores_are_pinned() -> None:
    """The baseline is deterministic: any change here is a real behaviour change."""
    result = evaluate(KeywordBaselineModel(), model_name="baseline", fixtures_dir=FIXTURES_DIR)
    assert result.category.accuracy == pytest.approx(19 / 26)
    assert result.failures == []
    assert "Category accuracy: 0.731" in format_report(result)


def test_failed_messages_are_scored_as_failures() -> None:
    model = ScriptedChatModel(script=[upsert(category="rejection", company="Brightloom")])
    result = evaluate(model, model_name="scripted", fixtures_dir=FIXTURES_DIR, max_steps=1)

    assert len(result.failures) == len(result.messages) - 1
    assert result.category.confusion["rejection"][FAILED] >= 1


def test_cli_writes_json_and_enforces_the_threshold(tmp_path: Path) -> None:
    output = tmp_path / "result.json"
    assert main(["--llm", "baseline", "--output", str(output), "--min-accuracy", "0.7"]) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["model"] == "keyword baseline"
    assert main(["--llm", "baseline", "--min-accuracy", "0.9"]) == EXIT_BELOW_THRESHOLD


def test_cli_reports_a_stale_recording(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")  # never used: replay only
    empty = tmp_path / "empty.json"
    empty.write_text("{}", encoding="utf-8")

    assert main(["--llm", "anthropic", "--replay", str(empty)]) == EXIT_STALE_RECORDING
    assert "stale" in capsys.readouterr().err


def test_cli_explains_an_unreachable_model(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A model whose server is down: every call raises, without touching the network.
    down = ScriptedChatModel(script=[ConnectionError("refused")] * 30)
    monkeypatch.setattr("apply_agent.evaluation.cli.make_chat_model", lambda *a, **k: down)

    assert main(["--llm", "ollama"]) == EXIT_MODEL_UNAVAILABLE
    assert "Is the model reachable?" in capsys.readouterr().err
