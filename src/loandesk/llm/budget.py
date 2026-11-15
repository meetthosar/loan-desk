"""Spend ledger. Every model call is recorded; a call is refused once the cap is reached."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path


class BudgetExceeded(RuntimeError):
    pass


class Ledger:
    def __init__(self, path: Path, cap_usd: float):
        self.path = path
        self.cap_usd = cap_usd
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write({"total_usd": 0.0, "by_model": {}, "by_tag": {}})

    def _read(self) -> dict:
        return json.loads(self.path.read_text())

    def _write(self, data: dict) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(self.path)

    @property
    def total(self) -> float:
        return self._read()["total_usd"]

    @property
    def remaining(self) -> float:
        return max(0.0, self.cap_usd - self.total)

    def check(self, estimated_usd: float = 0.0) -> None:
        total = self.total
        if total + estimated_usd > self.cap_usd:
            raise BudgetExceeded(
                f"Spend would reach ${total + estimated_usd:.4f}, over the ${self.cap_usd:.2f} cap "
                f"(spent so far ${total:.4f}). Raise LOANDESK_BUDGET_USD only on purpose."
            )

    def record(self, model: str, cost_usd: float, tag: str = "default") -> None:
        with self._lock:
            data = self._read()
            data["total_usd"] = round(data["total_usd"] + cost_usd, 6)
            data["by_model"][model] = round(data["by_model"].get(model, 0.0) + cost_usd, 6)
            data["by_tag"][tag] = round(data["by_tag"].get(tag, 0.0) + cost_usd, 6)
            data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            self._write(data)

    def summary(self) -> dict:
        return self._read() | {"cap_usd": self.cap_usd, "remaining_usd": round(self.remaining, 4)}
