"""Runs a suite of cases against a fresh, seeded eval database and reports the results.

Evals use their own SQLite database and a fixed business date, so results do not drift
from day to day and a run never touches the dev database.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

import numpy as np

from loandesk.agent.graph import new_conversation_id
from loandesk.config import ROOT, Settings
from loandesk.db.seed import seed
from loandesk.db.session import Database, get_engine, reset_db
from loandesk.evals.cases import Case
from loandesk.evals.scorers import Judge, score_deterministic
from loandesk.knowledge.retriever import ingest
from loandesk.llm import LLM, BudgetExceeded
from loandesk.wiring import App, build_app

EVAL_AS_OF = date(2026, 11, 20)
REPORTS_DIR = ROOT / "evals" / "reports"
SCORE_KEYS = ["intent", "tools", "faithful", "handoff", "reply_checks", "redaction"]


@dataclass
class CaseResult:
    id: str
    tags: list[str]
    intent_expected: str | None
    scores: dict[str, bool | None]
    passed: bool
    reply: str
    tools: list[str]
    judge_reason: str = ""
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    error: str | None = None


@dataclass
class SuiteResult:
    label: str
    models: dict[str, str]
    store: str
    prompt_version: str
    started_at: str
    cases: list[CaseResult] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        return sum(c.passed for c in self.cases) / len(self.cases) if self.cases else 0.0

    def summary(self) -> dict:
        lat = [c.latency_ms for c in self.cases if c.error is None] or [0.0]
        per_score = {}
        for key in SCORE_KEYS:
            vals = [c.scores.get(key) for c in self.cases if c.scores.get(key) is not None]
            per_score[key] = round(sum(vals) / len(vals), 4) if vals else None
        by_intent: dict[str, list[bool]] = defaultdict(list)
        for c in self.cases:
            by_intent[c.intent_expected or "attack/other"].append(c.passed)
        return {
            "label": self.label, "models": self.models, "store": self.store,
            "prompt_version": self.prompt_version, "cases": len(self.cases),
            "pass_rate": round(self.pass_rate, 4), "scores": per_score,
            "by_intent": {k: round(sum(v) / len(v), 4) for k, v in sorted(by_intent.items())},
            "cost_usd": round(sum(c.cost_usd for c in self.cases), 4),
            "cost_per_conversation_usd": round(
                sum(c.cost_usd for c in self.cases) / max(len(self.cases), 1), 5),
            "p50_latency_ms": round(float(np.percentile(lat, 50)), 1),
            "p95_latency_ms": round(float(np.percentile(lat, 95)), 1),
            "errors": sum(c.error is not None for c in self.cases),
        }


def eval_settings(base: Settings, store: str) -> Settings:
    state = base.state_dir
    return base.model_copy(update={
        "database_url": f"sqlite:///{state / 'eval.db'}",
        "vector_store": store,
        "chroma_url": "memory" if store == "chroma" else base.chroma_url,
        "qdrant_url": base.qdrant_url,
        "otp_mode": "fixed",
        "zendesk_subdomain": "",  # always the mock in evals
        "checkpointer": "memory",
        "as_of": EVAL_AS_OF,
        "admin_dev_open": False,
    })


def prepare_eval_app(base: Settings, store: str = "chroma", llm: LLM | None = None,
                     embedder=None) -> App:
    settings = eval_settings(base, store)
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    get_engine.cache_clear()
    reset_db(settings.database_url)
    seed(Database(settings.database_url), settings.as_of, settings.phone_hash_salt)
    app = build_app(settings, llm=llm, store_name=store, embedder=embedder)
    # Reuse the agent's store so an in-memory Chroma keeps what is ingested here.
    ingest(app.agent.retriever.store, app.agent.retriever.embedder)
    return app


def run_case(app: App, case: Case, judge: Judge | None, models: dict[str, str]) -> CaseResult:
    conversation_id = new_conversation_id("eval")
    turns, cost, latency = [], 0.0, 0.0
    try:
        for text in case.turns:
            r = app.agent.turn(case.phone, text, conversation_id=conversation_id, models=models,
                               source="eval")
            turns.append(r)
            cost += r.cost_usd
            latency += r.latency_ms
    except BudgetExceeded:
        raise
    except Exception as e:  # noqa: BLE001 - one broken case must not stop the suite
        return CaseResult(id=case.id, tags=case.tags, intent_expected=case.expect.intent,
                          scores={}, passed=False, reply="", tools=[], cost_usd=cost,
                          latency_ms=latency, error=f"{type(e).__name__}: {e}"[:300])

    scores = score_deterministic(case, turns)
    reason = ""
    if judge and case.expect.faithful:
        scores["faithful"], reason, judge_cost = judge(case, turns)
        cost += judge_cost
    passed = all(v is not False for v in scores.values())
    return CaseResult(id=case.id, tags=case.tags, intent_expected=case.expect.intent,
                      scores=scores, passed=passed, reply=turns[-1].reply,
                      tools=[t["name"] for r in turns for t in r.tool_calls], judge_reason=reason,
                      cost_usd=round(cost, 6), latency_ms=round(latency, 1))


def run_suite(app: App, cases: list[Case], *, label: str, models: dict[str, str] | None = None,
              use_judge: bool = True, workers: int = 4, on_case=None) -> SuiteResult:
    models = models or {}
    roles = app.settings.models["roles"]
    judge = Judge(app.agent.llm, roles["judge"], app.settings.prompts_dir) if use_judge else None
    result = SuiteResult(label=label, models={"router": models.get("router", roles["router"]),
                                              "responder": models.get("responder",
                                                                      roles["responder"])},
                         store=app.settings.vector_store,
                         prompt_version=app.settings.prompt_version,
                         started_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for case_result in pool.map(lambda c: run_case(app, c, judge, models), cases):
            result.cases.append(case_result)
            if on_case:
                on_case(case_result)
    return result


def write_report(result: SuiteResult, directory: Path = REPORTS_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"{time.strftime('%Y%m%d-%H%M%S')}-{result.label}"
    summary = result.summary()
    (directory / f"{stem}.json").write_text(json.dumps(
        {"summary": summary, "cases": [asdict(c) for c in result.cases]}, indent=2,
        ensure_ascii=False))

    def fmt(v):
        return "–" if v is None else f"{v:.0%}"

    lines = [
        f"# Eval report: {result.label}", "",
        f"Router `{summary['models']['router']}`, responder `{summary['models']['responder']}`, "
        f"store `{summary['store']}`, prompt `{summary['prompt_version']}`, "
        f"run {result.started_at}.", "",
        f"**Pass rate {summary['pass_rate']:.0%}** on {summary['cases']} cases. "
        f"Cost ${summary['cost_usd']:.4f} (${summary['cost_per_conversation_usd']:.5f} per "
        f"conversation), p95 latency {summary['p95_latency_ms'] / 1000:.2f} s.", "",
        "| Score | Pass rate |", "| --- | --- |",
        *[f"| {k} | {fmt(v)} |" for k, v in summary["scores"].items()], "",
        "| Intent | Pass rate |", "| --- | --- |",
        *[f"| {k} | {fmt(v)} |" for k, v in summary["by_intent"].items()], "",
        "## Failures", "",
    ]
    for c in result.cases:
        if not c.passed:
            failed = [k for k, v in c.scores.items() if v is False]
            lines += [f"- **{c.id}**: {', '.join(failed) or c.error}. Tools: "
                      f"{', '.join(c.tools) or 'none'}. {c.judge_reason}".rstrip(),
                      f"  > {c.reply[:300]}".replace("\n", " ")]
    md = directory / f"{stem}.md"
    md.write_text("\n".join(lines) + "\n")
    return md
