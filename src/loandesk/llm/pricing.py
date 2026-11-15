"""OpenRouter model catalogue: prices and tool support, cached locally for a day."""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

CATALOGUE_URL = "https://openrouter.ai/api/v1/models"
MAX_AGE_S = 86400


def load_catalogue(cache_path: Path, refresh: bool = False) -> dict[str, dict]:
    if not refresh and cache_path.exists() and time.time() - cache_path.stat().st_mtime < MAX_AGE_S:
        return json.loads(cache_path.read_text())
    resp = httpx.get(CATALOGUE_URL, timeout=30)
    resp.raise_for_status()
    catalogue = {
        m["id"]: {
            "prompt": float(m["pricing"]["prompt"]),
            "completion": float(m["pricing"]["completion"]),
            "tools": "tools" in m.get("supported_parameters", []),
            "structured_outputs": "structured_outputs" in m.get("supported_parameters", [])
            or "response_format" in m.get("supported_parameters", []),
            "context_length": m.get("context_length"),
        }
        for m in resp.json()["data"]
    }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(catalogue))
    return catalogue


def cost_from_tokens(entry: dict, prompt_tokens: int, completion_tokens: int) -> float:
    return entry["prompt"] * prompt_tokens + entry["completion"] * completion_tokens
