"""Benchmark the vector databases. Costs nothing: embeddings are local.

1. Quality: labelled questions (evals/retrieval/questions.yaml) -> recall@k and MRR, judged
   at section level. With the same embeddings and exact-enough search, every store should
   score about the same; a gap means an adapter bug or approximate search losing results.
2. Speed on the real corpus: write time, and query latency p50/p95 over repeated queries.
3. Filtered search: the same questions with a `doc` filter; checks every hit obeys it.
4. Scale: N random unit vectors (default 50,000) -> write throughput and query latency.
   Random vectors are fine for timing; they say nothing about quality.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass

import numpy as np
import yaml

from loandesk.config import ROOT, Settings
from loandesk.embeddings import get_embedder
from loandesk.knowledge.chunking import load_policies
from loandesk.knowledge.retriever import POLICIES_DIR
from loandesk.vectorstores import get_store
from loandesk.vectorstores.base import Chunk

QUESTIONS = ROOT / "evals" / "retrieval" / "questions.yaml"
REPORTS_DIR = ROOT / "evals" / "reports"


@dataclass
class StoreResult:
    store: str
    chunks: int
    write_s: float
    recall_at_k: float
    mrr: float
    filter_ok: bool
    query_p50_ms: float
    query_p95_ms: float
    scale_n: int = 0
    scale_write_s: float | None = None
    scale_query_p50_ms: float | None = None
    scale_query_p95_ms: float | None = None
    error: str | None = None


def _pcts(samples: list[float]) -> tuple[float, float]:
    return (round(float(np.percentile(samples, 50)), 2),
            round(float(np.percentile(samples, 95)), 2))


def bench_store(name: str, settings: Settings, chunks: list[Chunk], vectors: list[list[float]],
                questions: list[dict], qvecs: list[list[float]], *, k: int = 5,
                repeats: int = 5, scale_n: int = 50_000) -> StoreResult:
    store = get_store(name, settings, collection="bench")
    try:
        dim = len(vectors[0])
        store.reset(dim)
        t = time.perf_counter()
        store.upsert(chunks, vectors)
        write_s = time.perf_counter() - t

        hits_at_k, rr, timings, filter_ok = 0, 0.0, [], True
        for q, qv in zip(questions, qvecs, strict=True):
            for _ in range(repeats):
                t = time.perf_counter()
                hits = store.search(qv, k)
                timings.append((time.perf_counter() - t) * 1000)
            ranks = [i for i, h in enumerate(hits) if h.metadata.get("section_id") in q["relevant"]]
            hits_at_k += bool(ranks)
            rr += 1 / (ranks[0] + 1) if ranks else 0.0
            doc = q["relevant"][0].split("#")[0]
            filtered = store.search(qv, k, {"doc": doc})
            filter_ok &= bool(filtered) and all(h.metadata.get("doc") == doc for h in filtered)
        p50, p95 = _pcts(timings)
        result = StoreResult(store=name, chunks=store.count(), write_s=round(write_s, 3),
                             recall_at_k=round(hits_at_k / len(questions), 4),
                             mrr=round(rr / len(questions), 4), filter_ok=filter_ok,
                             query_p50_ms=p50, query_p95_ms=p95)

        if scale_n:
            rng = np.random.default_rng(7)
            vecs = rng.normal(size=(scale_n, dim)).astype(np.float32)
            vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
            synthetic = [Chunk(id=f"syn-{i}", text="synthetic",
                               metadata={"doc": f"d{i % 50}", "section_id": f"d{i % 50}#s"})
                         for i in range(scale_n)]
            store.reset(dim)
            t = time.perf_counter()
            store.upsert(synthetic, vecs.tolist())
            result.scale_write_s = round(time.perf_counter() - t, 2)
            result.scale_n = scale_n
            probes = rng.normal(size=(50, dim)).astype(np.float32)
            probes /= np.linalg.norm(probes, axis=1, keepdims=True)
            timings = []
            for p in probes.tolist():
                t = time.perf_counter()
                store.search(p, k)
                timings.append((time.perf_counter() - t) * 1000)
            result.scale_query_p50_ms, result.scale_query_p95_ms = _pcts(timings)
        return result
    except Exception as e:  # noqa: BLE001 - report a store that is down, keep benchmarking others
        return StoreResult(store=name, chunks=0, write_s=0, recall_at_k=0, mrr=0,
                           filter_ok=False, query_p50_ms=0, query_p95_ms=0,
                           error=f"{type(e).__name__}: {e}"[:300])
    finally:
        store.close()


def run_bench(settings: Settings, stores: list[str], *, k: int = 5, scale_n: int = 50_000,
              on_result=None) -> list[StoreResult]:
    embedder = get_embedder(settings.embedding_model)
    chunks = load_policies(POLICIES_DIR)
    vectors = embedder.embed_documents([c.text for c in chunks])
    questions = yaml.safe_load(QUESTIONS.read_text())
    qvecs = [embedder.embed_query(q["question"]) for q in questions]
    results = []
    for name in stores:
        results.append(bench_store(name, settings, chunks, vectors, questions, qvecs, k=k,
                                   scale_n=scale_n))
        if on_result:
            on_result(results[-1])
    return results


def write_bench_report(results: list[StoreResult], settings: Settings, k: int) -> str:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stem = REPORTS_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-vector-bench"
    stem.with_suffix(".json").write_text(json.dumps([asdict(r) for r in results], indent=2))
    scale_n = max((r.scale_n for r in results), default=0)
    lines = [
        "# Vector database benchmark", "",
        f"Embeddings `{settings.embedding_model}` (local), cosine similarity, k = {k}. "
        f"Query latency includes the network round trip to each server.", "",
        f"| Store | Recall@{k} | MRR | Filter | Query p50 / p95 (policies) | "
        f"Write {scale_n:,} vectors | Query p50 / p95 at {scale_n:,} |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in results:
        if r.error:
            lines.append(f"| {r.store} | error: {r.error} | | | | | |")
            continue
        scale = (f"{r.scale_write_s:.1f} s" if r.scale_write_s is not None else "–",
                 f"{r.scale_query_p50_ms:.1f} / {r.scale_query_p95_ms:.1f} ms"
                 if r.scale_query_p50_ms is not None else "–")
        lines.append(f"| {r.store} | {r.recall_at_k:.0%} | {r.mrr:.3f} | "
                     f"{'ok' if r.filter_ok else 'FAIL'} | "
                     f"{r.query_p50_ms:.1f} / {r.query_p95_ms:.1f} ms | {scale[0]} | {scale[1]} |")
    stem.with_suffix(".md").write_text("\n".join(lines) + "\n")
    return "\n".join(lines)
