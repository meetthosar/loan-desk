"""One client for every model, through OpenRouter's OpenAI-compatible API.

Each call checks the budget first, then records the real cost OpenRouter reports
(or a token-based estimate when the response carries no cost) and writes a trace span.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from openai import OpenAI

from loandesk.config import Settings
from loandesk.llm.budget import Ledger
from loandesk.llm.pricing import cost_from_tokens, load_catalogue
from loandesk.tracing import Tracer


@dataclass
class LLMResult:
    model: str
    message: dict[str, Any]  # OpenAI-format assistant message
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    raw: Any = field(default=None, repr=False)

    @property
    def content(self) -> str:
        return self.message.get("content") or ""

    @property
    def tool_calls(self) -> list[dict]:
        return self.message.get("tool_calls") or []


class LLM(Protocol):
    def chat(
        self,
        model: str,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        response_format: dict | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2000,
        tag: str = "default",
    ) -> LLMResult: ...


class OpenRouterLLM:
    def __init__(self, settings: Settings, ledger: Ledger, tracer: Tracer):
        self.settings = settings
        self.ledger = ledger
        self.tracer = tracer
        self.providers: dict = settings.models.get("providers") or {}
        self._client: OpenAI | None = None
        self._catalogue: dict | None = None

    @property
    def client(self) -> OpenAI:
        # Created on first use, so the API (webhooks, dashboard) starts without a key.
        if self._client is None:
            if not self.settings.openrouter_api_key:
                raise RuntimeError("OPENROUTER_API_KEY is not set. Copy .env.example to .env.")
            self._client = OpenAI(
                api_key=self.settings.openrouter_api_key,
                base_url=self.settings.openrouter_base_url,
                max_retries=3,  # exponential backoff on 429 and 5xx
                timeout=60,
                default_headers={"X-Title": "LoanDesk"},
            )
        return self._client

    def _price(self, model: str, prompt_tokens: int, completion_tokens: int) -> float:
        if self._catalogue is None:
            self._catalogue = load_catalogue(self.settings.state_dir / "catalogue.json")
        entry = self._catalogue.get(model)
        return cost_from_tokens(entry, prompt_tokens, completion_tokens) if entry else 0.0

    def chat(self, model, messages, *, tools=None, response_format=None, temperature=0.0,
             max_tokens=2000, tag="default") -> LLMResult:
        self.ledger.check()
        extra_body: dict[str, Any] = {"usage": {"include": True},
                                      **(self.settings.models.get("request_defaults") or {})}
        if model in self.providers:
            extra_body["provider"] = self.providers[model]
        kwargs: dict[str, Any] = {}
        if tools:
            kwargs["tools"] = tools
        if response_format:
            kwargs["response_format"] = response_format

        start = time.perf_counter()
        resp = self.client.chat.completions.create(
            model=model, messages=messages, temperature=temperature, max_tokens=max_tokens,
            extra_body=extra_body, **kwargs,
        )
        latency_ms = (time.perf_counter() - start) * 1000

        usage = resp.usage
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0
        details = getattr(usage, "prompt_tokens_details", None)
        cached = (getattr(details, "cached_tokens", 0) or 0) if details else 0
        cost = getattr(usage, "cost", None)
        if cost is None:
            cost = self._price(model, prompt_tokens, completion_tokens)
        self.ledger.record(model, float(cost), tag)

        msg = resp.choices[0].message.model_dump(exclude_none=True)
        result = LLMResult(model=model, message=msg, prompt_tokens=prompt_tokens,
                           completion_tokens=completion_tokens, cached_tokens=cached,
                           cost_usd=float(cost), latency_ms=latency_ms, raw=resp)
        self.tracer.span("llm", model=model, tag=tag, prompt_tokens=prompt_tokens,
                         completion_tokens=completion_tokens, cached_tokens=cached,
                         cost_usd=float(cost), latency_ms=round(latency_ms, 1),
                         tool_calls=[c["function"]["name"] for c in result.tool_calls])
        return result
