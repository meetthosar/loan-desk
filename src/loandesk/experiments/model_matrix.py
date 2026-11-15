"""Compare candidate models on the same eval cases. Each candidate runs as router and
responder; the judge stays fixed. The run is costed before it starts and refused if the
estimate would take spend over the cap."""

from __future__ import annotations

import json
import time

from loandesk.config import Settings
from loandesk.evals.cases import Case
from loandesk.evals.runner import REPORTS_DIR, SuiteResult, run_suite, write_report
from loandesk.llm.pricing import cost_from_tokens, load_catalogue
from loandesk.wiring import App


def estimate(settings: Settings, candidates: dict[str, str], n_cases: int,
             use_judge: bool = True) -> dict[str, float]:
    catalogue = load_catalogue(settings.state_dir / "catalogue.json")
    est = settings.models["estimate"]
    judge = catalogue[settings.models["roles"]["judge"]]
    judge_cost = cost_from_tokens(judge, est["judge_input_tokens_per_case"],
                                  est["judge_output_tokens_per_case"]) if use_judge else 0.0
    out = {}
    for name, model in candidates.items():
        if model not in catalogue:
            raise ValueError(f"{model} is not on OpenRouter. Run `loandesk models check`.")
        per_case = cost_from_tokens(catalogue[model], est["input_tokens_per_case"],
                                    est["output_tokens_per_case"])
        out[name] = round((per_case + judge_cost) * n_cases, 4)
    return out


def run_matrix(app: App, cases: list[Case], candidates: dict[str, str], *,
               use_judge: bool = True, workers: int = 4, on_case=None) -> list[SuiteResult]:
    results = []
    for name, model in candidates.items():
        results.append(run_suite(app, cases, label=f"matrix-{name}",
                                 models={"router": model, "responder": model},
                                 use_judge=use_judge, workers=workers, on_case=on_case))
        write_report(results[-1])
    return results


def write_comparison(results: list[SuiteResult]) -> str:
    rows = [r.summary() for r in results]
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stem = REPORTS_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-model-comparison"
    stem.with_suffix(".json").write_text(json.dumps(rows, indent=2))

    def pct(v):
        return "–" if v is None else f"{v:.0%}"

    lines = [
        "# Model comparison", "",
        f"{rows[0]['cases']} cases, store `{rows[0]['store']}`, prompt "
        f"`{rows[0]['prompt_version']}`. Same judge for every model.", "",
        "| Model | Pass rate | Intent | Tools | Faithful | Handoff | Cost / conversation "
        "| p95 latency | Errors |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in sorted(rows, key=lambda r: -r["pass_rate"]):
        s = r["scores"]
        lines.append(
            f"| `{r['models']['responder']}` | **{pct(r['pass_rate'])}** | {pct(s['intent'])} | "
            f"{pct(s['tools'])} | {pct(s['faithful'])} | {pct(s['handoff'])} | "
            f"${r['cost_per_conversation_usd']:.5f} | {r['p95_latency_ms'] / 1000:.2f} s | "
            f"{r['errors']} |")
    stem.with_suffix(".md").write_text("\n".join(lines) + "\n")
    return "\n".join(lines)
