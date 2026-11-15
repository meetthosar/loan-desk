"""Trace spans as JSON lines in .loandesk/traces/, one file per day.

Every model call and tool call writes a span with tokens, latency and cost.
The conversation id comes from a context variable set at the start of each turn.
"""

from __future__ import annotations

import contextvars
import json
import threading
import time
from pathlib import Path

current_conversation: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_conversation", default="-"
)


class Tracer:
    def __init__(self, directory: Path | None):
        self.directory = directory
        self._lock = threading.Lock()
        if directory:
            directory.mkdir(parents=True, exist_ok=True)

    def span(self, kind: str, **fields) -> None:
        if not self.directory:
            return
        record = {"ts": time.time(), "kind": kind, "conversation": current_conversation.get(),
                  **fields}
        path = self.directory / f"{time.strftime('%Y-%m-%d')}.jsonl"
        with self._lock, path.open("a") as f:
            f.write(json.dumps(record, default=str) + "\n")
