"""Record real LLM responses once, replay them for free afterwards.

``RecordingCache`` plugs into LangChain's model cache. The key is a hash of
the exact prompt (every message, including tool results) and the model
configuration (model id, parameters, bound tools). Any change to the prompt,
the tools or the model therefore misses the cache.

* record mode: a miss calls the real model and stores the response.
* replay mode: a miss raises ``ReplayMissError``, so a stale recording fails
  loudly instead of silently calling a paid API (or failing half the eval).

Recordings are committed to the repo; they only ever contain fixture data.
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


class RecordingCache(BaseCache):
    def __init__(self, path: Path, *, replay_only: bool) -> None:
        self._path = path
        self._replay_only = replay_only
        self._entries: dict[str, list[dict[str, Any]]] = (
            json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        )
        self.hits = 0
        self.misses = 0

    @staticmethod
    def key(prompt: str, llm_string: str) -> str:
        return hashlib.sha256(f"{llm_string}\n{prompt}".encode()).hexdigest()

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
