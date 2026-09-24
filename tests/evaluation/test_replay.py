from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from apply_agent.evaluation.replay import RecordingCache, ReplayMissError
from tests.agent.helpers import ScriptedChatModel, upsert

PROMPT = [HumanMessage("Assess this email")]


def _record(path: Path, reply: AIMessage) -> None:
    model = ScriptedChatModel(script=[reply], cache=RecordingCache(path, replay_only=False))
    model.invoke(PROMPT)


def test_replay_returns_the_recorded_response_without_calling_the_model(tmp_path: Path) -> None:
    path = tmp_path / "rec.json"
    reply = upsert(category="offer")
    reply.usage_metadata = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
    _record(path, reply)

    cache = RecordingCache(path, replay_only=True)
    replayed = ScriptedChatModel(script=[], cache=cache).invoke(PROMPT)

    assert isinstance(replayed, AIMessage)
    assert replayed.tool_calls == reply.tool_calls
    assert replayed.usage_metadata is not None
    assert replayed.usage_metadata["input_tokens"] == 10
    assert replayed.usage_metadata["output_tokens"] == 5
    assert cache.hits == 1


def test_a_changed_prompt_is_a_miss_that_fails_loudly(tmp_path: Path) -> None:
    path = tmp_path / "rec.json"
    _record(path, upsert())
    model = ScriptedChatModel(script=[], cache=RecordingCache(path, replay_only=True))
    with pytest.raises(ReplayMissError):
        model.invoke([HumanMessage("A different email")])


def test_record_mode_only_calls_the_model_on_a_miss(tmp_path: Path) -> None:
    path = tmp_path / "rec.json"
    _record(path, upsert())
    model = ScriptedChatModel(script=[], cache=RecordingCache(path, replay_only=False))
    model.invoke(PROMPT)  # served from the recording; the empty script is never touched
    assert model.calls == []


def test_recordings_are_stable_json(tmp_path: Path) -> None:
    path = tmp_path / "rec.json"
    _record(path, upsert())
    first = path.read_text(encoding="utf-8")
    RecordingCache(path, replay_only=False)._save()
    assert path.read_text(encoding="utf-8") == first
