"""Record real LLM responses once, replay them for free afterwards.

``RecordingCache`` plugs into LangChain's model cache. The key is built from:

* a model fingerprint supplied by the caller (provider, model, decoding
  parameters). LangChain's own ``llm_string`` is not enough: for some chat
  models it omits parameters such as temperature or context size.
* the bound tools (from ``llm_string``).
* a canonical form of the prompt: each message's role, content and tool calls
  (name and arguments). Ids and response metadata are dropped, because
  LangChain assigns a random id to every model reply, and that reply becomes
  part of the next step's prompt.

In record mode a miss calls the real model and stores the response. In replay
mode a miss raises ``ReplayMissError``, so a stale recording fails loudly
instead of silently calling a paid or slow model.
"""

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from langchain_core.caches import RETURN_VAL_TYPE, BaseCache
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, Generation


class ReplayMissError(LookupError):
    """The recording has no response for this prompt: re-record with a live model."""


def canonical_prompt(prompt: str) -> str:
    """The parts of a serialised message list that determine the model's answer."""
    try:
        messages = json.loads(prompt)
    except json.JSONDecodeError:
        return prompt
    canonical = []
    for message in messages:
        kwargs: dict[str, Any] = message.get("kwargs", {})
        canonical.append(
            {
                "role": message.get("id", ["?"])[-1],
                "content": kwargs.get("content"),
                "tool_calls": [
                    {"name": call.get("name"), "args": call.get("args")}
                    for call in kwargs.get("tool_calls", [])
                ],
            }
        )
    return json.dumps(canonical, sort_keys=True, ensure_ascii=False)


class RecordingCache(BaseCache):
    def __init__(self, path: Path, *, replay_only: bool, fingerprint: str) -> None:
        self._path = path
        self._replay_only = replay_only
        self._fingerprint = fingerprint
        self._entries: dict[str, list[dict[str, Any]]] = (
            json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        )
        self.hits = 0
        self.misses = 0

    def key(self, prompt: str, llm_string: str) -> str:
        material = "\n".join([self._fingerprint, llm_string, canonical_prompt(prompt)])
        return hashlib.sha256(material.encode()).hexdigest()

    def lookup(self, prompt: str, llm_string: str) -> RETURN_VAL_TYPE | None:
        entry = self._entries.get(self.key(prompt, llm_string))
        if entry is None:
            self.misses += 1
            if self._replay_only:
                raise ReplayMissError(f"no recorded response in {self._path}")
            return None
        self.hits += 1
        return [ChatGeneration(message=AIMessage.model_validate(m)) for m in entry]

    def update(self, prompt: str, llm_string: str, return_val: Sequence[Generation]) -> None:
        messages = [g.message for g in return_val if isinstance(g, ChatGeneration)]
        self._entries[self.key(prompt, llm_string)] = [
            m.model_dump(mode="json") for m in messages if isinstance(m, AIMessage)
        ]
        self._save()

    def clear(self, **kwargs: Any) -> None:
        self._entries.clear()
        self._save()

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(self._entries, indent=1, sort_keys=True, ensure_ascii=False)
        self._path.write_text(text + "\n", encoding="utf-8", newline="\n")
