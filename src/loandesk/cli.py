"""loandesk command line. Run `loandesk --help` for the list."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer
import yaml
from rich.console import Console
from rich.table import Table

from loandesk.config import ROOT, get_settings

app = typer.Typer(no_args_is_help=True, add_completion=False)
db_app = typer.Typer(no_args_is_help=True, help="Mock core banking database.")
eval_app = typer.Typer(no_args_is_help=True, help="Eval suite and release gate.")
exp_app = typer.Typer(no_args_is_help=True, help="Model and vector database experiments.")
models_app = typer.Typer(no_args_is_help=True, help="OpenRouter models.")
app.add_typer(db_app, name="db")
app.add_typer(eval_app, name="eval")
app.add_typer(exp_app, name="experiment")
app.add_typer(models_app, name="models")
console = Console()


def _confirm_spend(estimates: dict[str, float], yes: bool) -> None:
    from loandesk.wiring import make_ledger

    ledger = make_ledger(get_settings())
    total = sum(estimates.values())
    table = Table("Run", "Estimated cost")
    for name, cost in estimates.items():
        table.add_row(name, f"${cost:.4f}")
    table.add_row("[bold]total[/bold]", f"[bold]${total:.4f}[/bold]")
    console.print(table)
    console.print(f"Spent so far ${ledger.total:.4f} of ${ledger.cap_usd:.2f}; "
                  f"${ledger.remaining:.4f} left.")
    ledger.check(total)
    if not yes and not typer.confirm("Run it?"):
        raise typer.Exit(1)


# --- database & knowledge -------------------------------------------------------------------

@db_app.command("seed")
def db_seed(borrowers: int = 1000,
            if_empty: bool = typer.Option(False, help="Do nothing if borrowers exist")) -> None:
    """Drop and recreate the tables, then load synthetic borrowers."""
    from sqlalchemy import func, inspect, select

    from loandesk.db.models import Borrower
    from loandesk.db.seed import SCENARIOS, phone_for, seed
    from loandesk.db.session import Database, get_engine, reset_db

    s = get_settings()
    db = Database(s.database_url)
    if if_empty and inspect(get_engine(s.database_url)).has_table("borrowers"):
        with db.session() as session:
            if session.scalar(select(func.count()).select_from(Borrower)):
                console.print("Borrowers already loaded; not reseeding.")
                return
    reset_db(s.database_url)
    seed(db, s.as_of, s.phone_hash_salt, borrowers)
    console.print(f"Seeded {borrowers} borrowers as of {s.as_of}. Scenario phones:")
    for n, desc in SCENARIOS.items():
        console.print(f"  {phone_for(n)}  {desc}")


@app.command()
def ingest(
    store: str = typer.Option(None, help="pgvector | qdrant | chroma | pinecone | all"),
) -> None:
    """Chunk the policy pages, embed them locally and write them to the vector store(s)."""
    from loandesk.embeddings import get_embedder
    from loandesk.knowledge.retriever import ingest as do_ingest
    from loandesk.vectorstores import STORES, get_store

    s = get_settings()
    names = STORES if store == "all" else [store or s.vector_store]
    if store == "all" and not s.pinecone_api_key:
        names = [n for n in names if n != "pinecone"]
    embedder = get_embedder(s.embedding_model)
    for name in names:
        st = get_store(name, s)
        stats = do_ingest(st, embedder)
        console.print(f"{name}: {stats['chunks']} chunks, embed {stats['embed_s']:.2f} s, "
                      f"write {stats['write_s']:.2f} s")
        st.close()


# --- talking to the agent -------------------------------------------------------------------

@app.command()
def chat(phone: str = "+919000000001", model: str = typer.Option(None, help="Override "
         "router and responder")) -> None:
    """Chat with the agent in the terminal as a borrower."""
    from loandesk.wiring import build_app

    loandesk = build_app(get_settings())
    models = {"router": model, "responder": model} if model else None
    console.print(f"Chatting as {phone}. Empty line to quit.")
    while text := console.input("[bold]you>[/bold] ").strip():
        r = loandesk.agent.turn(phone, text, models=models)
        console.print(f"[green]loandesk>[/green] {r.reply}")
        console.print(f"[dim]intent={r.intent} conf={r.confidence} handoff={r.handoff_reason} "
                      f"tools={[t['name'] for t in r.tool_calls]} flags={r.flags} "
                      f"${r.cost_usd:.5f} {r.latency_ms:.0f} ms[/dim]")
    loandesk.close()


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000, reload: bool = False) -> None:
    """Run the HTTP API (chat, payment webhook, admin dashboard)."""
    import uvicorn

    uvicorn.run("loandesk.api.main:api", host=host, port=port, reload=reload)


@app.command()
def spend() -> None:
    """Show model spend so far against the cap."""
    from loandesk.wiring import make_ledger

    console.print_json(json.dumps(make_ledger(get_settings()).summary()))


# --- evals ----------------------------------------------------------------------------------

@eval_app.command("run")
def eval_run(
    cases: str = typer.Option("seed", help="seed (20) | all (60) | a file name in evals/cases"),
    model: str = typer.Option(None, help="Run one model as router and responder"),
    store: str = typer.Option("chroma", help="Vector store for the run"),
    judge: bool = typer.Option(True, help="Score faithfulness with the LLM judge"),
    gate: bool = typer.Option(False, help="Exit 1 if the pass rate is below evals/gate.yaml"),
    workers: int = 4,
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the cost confirmation"),
) -> None:
    """Run the eval suite and write a report to evals/reports/."""
    from loandesk.evals.cases import load_cases
    from loandesk.evals.runner import prepare_eval_app, run_suite, write_report
    from loandesk.experiments.model_matrix import estimate

    s = get_settings()
    case_list = load_cases(cases)
    responder = model or s.models["roles"]["responder"]
    _confirm_spend(estimate(s, {responder: responder}, len(case_list), judge), yes)
    loandesk = prepare_eval_app(s, store)
    models = {"router": model, "responder": model} if model else None
    result = run_suite(loandesk, case_list, label=f"eval-{cases}", models=models,
                       use_judge=judge, workers=workers,
                       on_case=lambda c: console.print(
                           f"{'[green]PASS' if c.passed else '[red]FAIL'}[/] {c.id} "
                           f"[dim]{', '.join(k for k, v in c.scores.items() if v is False)}"
                           f"{c.error or ''}[/dim]"))
    report = write_report(result)
    summary = result.summary()
    console.print(f"\nPass rate [bold]{summary['pass_rate']:.0%}[/bold], cost "
                  f"${summary['cost_usd']:.4f}. Report: {report}")
    loandesk.close()
    if gate:
        bar = (yaml.safe_load((ROOT / "evals" / "gate.yaml").read_text()) or {}).get(
            "min_pass_rate")
        if bar is None:
            console.print("[yellow]No gate set in evals/gate.yaml yet; not failing.[/yellow]")
        elif summary["pass_rate"] < bar:
            console.print(f"[red]Gate failed: {summary['pass_rate']:.0%} < {bar:.0%}[/red]")
            sys.exit(1)
        else:
            console.print(f"[green]Gate passed ({bar:.0%}).[/green]")


@eval_app.command("calibrate")
def eval_calibrate(report: Path = typer.Argument(..., help="An eval report .json"),
                   labels: Path = ROOT / "evals" / "labels.yaml") -> None:
    """Compare the judge's verdicts in a report with your hand labels."""
    from loandesk.experiments.calibrate import calibrate

    console.print_json(json.dumps(calibrate(report, labels)))


# --- experiments ----------------------------------------------------------------------------

@exp_app.command("models")
def exp_models(
    cases: str = typer.Option("seed", help="seed (20) | all (60)"),
    only: str = typer.Option(None, help="Comma-separated matrix names, e.g. claude,cheap"),
    store: str = "chroma",
    judge: bool = True,
    workers: int = 4,
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Run the eval cases on every model in config/models.yaml `matrix`."""
    from loandesk.evals.cases import load_cases
    from loandesk.evals.runner import prepare_eval_app
    from loandesk.experiments.model_matrix import estimate, run_matrix, write_comparison

    s = get_settings()
    candidates = s.models["matrix"]
    if only:
        candidates = {k: v for k, v in candidates.items() if k in only.split(",")}
    case_list = load_cases(cases)
    _confirm_spend(estimate(s, candidates, len(case_list), judge), yes)
    loandesk = prepare_eval_app(s, store)
    results = run_matrix(loandesk, case_list, candidates, use_judge=judge, workers=workers,
                         on_case=lambda c: console.print(
                             f"{'[green]PASS' if c.passed else '[red]FAIL'}[/] {c.id}"))
    console.print(write_comparison(results))
    loandesk.close()


@exp_app.command("stores")
def exp_stores(
    stores: str = typer.Option("pgvector,qdrant,chroma,pinecone"),
    scale: int = typer.Option(50_000, help="Random vectors for the scale test; 0 to skip"),
    k: int = 5,
) -> None:
    """Benchmark the vector databases (no model calls, no cost)."""
    from loandesk.experiments.vector_bench import run_bench, write_bench_report

    s = get_settings()
    names = [n for n in stores.split(",") if n != "pinecone" or s.pinecone_api_key]
    if "pinecone" in stores and "pinecone" not in names:
        console.print("[yellow]Skipping pinecone: LOANDESK_PINECONE_API_KEY is not set.[/yellow]")
    results = run_bench(s, names, k=k, scale_n=scale,
                        on_result=lambda r: console.print(f"{r.store}: done"
                                                          + (f" ({r.error})" if r.error else "")))
    console.print(write_bench_report(results, s, k))


@exp_app.command("simulate")
def exp_simulate(
    n: int = 500,
    model: str = typer.Option("google/gemini-3.5-flash-lite",
                              help="Router and responder for the run; '' for the configured ones"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Send N synthetic conversations through the agent to fill the dashboard."""
    from loandesk.experiments.model_matrix import estimate
    from loandesk.experiments.simulate import simulate
    from loandesk.wiring import build_app

    s = get_settings()
    chosen = model or s.models["roles"]["responder"]
    _confirm_spend(estimate(s, {chosen: chosen}, n, use_judge=False), yes)
    loandesk = build_app(s)
    done = {"n": 0}

    def tick(_):
        done["n"] += 1
        if done["n"] % 50 == 0:
            console.print(f"{done['n']}/{n}")

    simulate(loandesk, n, model or None, on_turn=tick)
    from loandesk.api.kpis import compute_kpis

    console.print_json(json.dumps(compute_kpis(loandesk.db, "simulate")))
    loandesk.close()


# --- models ---------------------------------------------------------------------------------

@models_app.command("check")
def models_check(refresh: bool = True) -> None:
    """Check every configured model exists on OpenRouter and supports tool calling."""
    from loandesk.llm.pricing import load_catalogue

    s = get_settings()
    catalogue = load_catalogue(s.state_dir / "catalogue.json", refresh=refresh)
    cfg = s.models
    rows = {f"role:{k}": v for k, v in cfg["roles"].items()}
    rows |= {f"matrix:{k}": v for k, v in cfg["matrix"].items()}
    if cfg.get("routing", {}).get("cheap_responder"):
        rows["routing:cheap"] = cfg["routing"]["cheap_responder"]
    table = Table("Slot", "Model", "On OpenRouter", "Tools", "$ / 1M in", "$ / 1M out")
    bad = False
    for slot, model in rows.items():
        entry = catalogue.get(model)
        if not entry:
            bad = True
            table.add_row(slot, model, "[red]no[/red]", "", "", "")
            continue
        needs_tools = not slot.startswith("role:judge")
        tools_ok = entry["tools"] or not needs_tools
        bad |= not tools_ok
        table.add_row(slot, model, "yes", "yes" if entry["tools"] else "[red]no[/red]",
                      f"{entry['prompt'] * 1e6:.2f}", f"{entry['completion'] * 1e6:.2f}")
    console.print(table)
    if bad:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
