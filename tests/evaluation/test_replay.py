from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from apply_agent.agent.llm import model_fingerprint
from apply_agent.config import Settings
from apply_agent.evaluation.replay import RecordingCache, ReplayMissError, canonical_prompt
from apply_agent.evaluation.run import evaluate
from tests.agent.helpers import ScriptedChatModel, get_thread, upsert
from tests.conftest import FIXTURES_DIR

PROMPT = [HumanMessage("Assess this email")]


def _cache(path: Path, *, replay_only: bool, fingerprint: str = "model-a") -> RecordingCache:
    return RecordingCache(path, replay_only=replay_only, fingerprint=fingerprint)


def _record(path: Path, reply: AIMessage, fingerprint: str = "model-a") -> None:
    model = ScriptedChatModel(
        script=[reply], cache=_cache(path, replay_only=False, fingerprint=fingerprint)
    )
    model.invoke(PROMPT)


def test_replay_returns_the_recorded_response_without_calling_the_model(tmp_path: Path) -> None:
    path = tmp_path / "rec.json"
    reply = upsert(category="offer")
    reply.usage_metadata = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
    _record(path, reply)

    cache = _cache(path, replay_only=True)
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
    model = ScriptedChatModel(script=[], cache=_cache(path, replay_only=True))
    with pytest.raises(ReplayMissError):
        model.invoke([HumanMessage("A different email")])


def test_a_different_model_fingerprint_is_a_miss(tmp_path: Path) -> None:
    path = tmp_path / "rec.json"
    _record(path, upsert(), fingerprint="model-a")
    model = ScriptedChatModel(
        script=[], cache=_cache(path, replay_only=True, fingerprint="model-b")
    )
    with pytest.raises(ReplayMissError):
        model.invoke(PROMPT)


def test_record_mode_only_calls_the_model_on_a_miss(tmp_path: Path) -> None:
    path = tmp_path / "rec.json"
    _record(path, upsert())
    model = ScriptedChatModel(script=[], cache=_cache(path, replay_only=False))
    model.invoke(PROMPT)  # served from the recording; the empty script is never touched
    assert model.calls == []


def test_canonical_prompt_ignores_ids_and_metadata() -> None:
    from langchain_core.load import dumps

    def conversation(message_id: str, call_id: str) -> str:
        reply = AIMessage(
            "",
            id=message_id,
            tool_calls=[{"name": "get_thread", "args": {}, "id": call_id, "type": "tool_call"}],
            response_metadata={"total_duration": message_id},
        )
        return dumps([*PROMPT, reply, ToolMessage("thread", tool_call_id=call_id)])

    assert canonical_prompt(conversation("run-1", "c-1")) == canonical_prompt(
        conversation("run-2", "c-2")
    )


def _thread_then_upsert(conversation: list[BaseMessage]) -> AIMessage:
    """Every email takes two steps: read the thread, then record."""
    if isinstance(conversation[-1], ToolMessage):
        return upsert(category="rejection", company="Acme")
    return get_thread()


def test_multi_step_conversations_replay_through_the_real_graph(tmp_path: Path) -> None:
    """Regression: LangChain gives every reply a random id that ends up in the next prompt."""
    path = tmp_path / "rec.json"
    steps = [_thread_then_upsert] * (2 * len(list((FIXTURES_DIR / "emails").glob("*.eml"))))
    live = ScriptedChatModel(script=list(steps), cache=_cache(path, replay_only=False))
    recorded = evaluate(live, model_name="live", fixtures_dir=FIXTURES_DIR)

    offline = ScriptedChatModel(script=[], cache=_cache(path, replay_only=True))
    replayed = evaluate(offline, model_name="replay", fixtures_dir=FIXTURES_DIR)

    assert recorded.failures == replayed.failures == []
    assert replayed.category.accuracy == recorded.category.accuracy
    assert offline.calls == []


@pytest.mark.parametrize(
    "change",
    [
        {"APPLY_AGENT_OLLAMA_MODEL": "llama3.1:8b"},
        {"APPLY_AGENT_OLLAMA_NUM_CTX": "16384"},
        {"APPLY_AGENT_LLM": "anthropic"},
    ],
)
def test_fingerprint_changes_with_anything_that_changes_answers(change: dict[str, str]) -> None:
    base = {"APPLY_AGENT_LLM": "ollama"}
    assert model_fingerprint(Settings.from_env(base)) != model_fingerprint(
        Settings.from_env(base | change)
    )


def test_fingerprint_ignores_settings_that_do_not_change_answers() -> None:
    base = {"APPLY_AGENT_LLM": "ollama"}
    moved = base | {"APPLY_AGENT_OLLAMA_BASE_URL": "http://gpu-box:11434"}
    assert model_fingerprint(Settings.from_env(base)) == model_fingerprint(Settings.from_env(moved))


def test_recordings_are_stable_json(tmp_path: Path) -> None:
    path = tmp_path / "rec.json"
    _record(path, upsert())
    first = path.read_text(encoding="utf-8")
    _cache(path, replay_only=False)._save()
    assert path.read_text(encoding="utf-8") == first
