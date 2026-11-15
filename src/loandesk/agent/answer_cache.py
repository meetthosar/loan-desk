"""Answer cache for general policy questions (layer 5). In memory, with an expiry time.

The key includes the prompt version and the model, so a rollback or a model change
never serves an answer written under the old setup.
"""

from __future__ import annotations

import re
import threading
import time


def _normalise(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", "", text.lower()).split())


class AnswerCache:
    def __init__(self, cfg: dict):
        self.enabled = bool(cfg.get("enabled"))
        self.ttl = float(cfg.get("ttl_seconds", 86400))
        self._data: dict[tuple, tuple[float, str]] = {}
        self._lock = threading.Lock()
        self.hits = self.misses = 0

    def _key(self, key: tuple) -> tuple:
        return (_normalise(key[0]), *key[1:])

    def get(self, key: tuple) -> str | None:
        with self._lock:
            entry = self._data.get(self._key(key))
            if entry and entry[0] > time.time():
                self.hits += 1
                return entry[1]
            self.misses += 1
            return None

    def put(self, key: tuple, value: str) -> None:
        with self._lock:
            self._data[self._key(key)] = (time.time() + self.ttl, value)
