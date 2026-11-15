# Experiments

## Model comparison

`loandesk experiment models --cases seed` runs the eval cases once per candidate in `config/models.yaml` `matrix`. Each candidate plays both router and responder. The judge stays `deepseek/deepseek-v4-pro` for every run, so the only thing that changes is the model under test.

Reported per model: pass rate, the four scores (intent, tools, faithful, handoff), cost per conversation (actual, from OpenRouter), p95 latency, errors. Results go to `evals/reports/*-model-comparison.md`.

Things to know before reading the results:
- **20 cases is a small sample.** One case is 5 points of pass rate. Compare on all 60 before choosing a model, and treat gaps under 10 points as noise.
- **Pin providers** in `providers:` for open-weight models; OpenRouter may otherwise serve the same model from different hosts on different runs.
- **The judge has a family bias risk.** It comes from a fifth family, but check it against your own labels (`loandesk eval calibrate`) before trusting faithfulness scores.
- Tool-calling quality differs most between models. Expect most failures in `tools` and `handoff`, not `intent`.

### Suggested $10 budget

| Run | Estimate |
| --- | --- |
| Development: a few seed runs on the cheap model | ~$0.50 |
| Seed comparison, all 4 models | ~$1.40 |
| Full 60-case comparison, all 4 models | ~$4.10 |
| 500 simulated conversations for the dashboard | ~$2.20 |
| Left over | ~$1.80 |

Estimates use the token counts in `config/models.yaml` `estimate`. Update them after your first real run; the ledger records actual spend.

## Vector database benchmark

`loandesk experiment stores` (free; no model calls):

1. **Quality.** 35 labelled questions in `evals/retrieval/questions.yaml`, scored at section level: recall@5 and MRR.
2. **Speed on the real corpus.** Query p50/p95 over 5 repeats of each question.
3. **Filters.** The same questions filtered to one policy page; every hit must obey the filter.
4. **Scale.** 50,000 random unit vectors: write time, then query p50/p95.

### First run (13 Nov 2026, MacBook, Docker Desktop, default settings)

| Store | Recall@5 | MRR | Filter | Query p50 / p95 (60 chunks) | Write 50,000 vectors | Query p50 / p95 at 50,000 |
| --- | --- | --- | --- | --- | --- | --- |
| pgvector | 97% | 0.890 | ok | 0.3 / 0.5 ms | 127.6 s | 1.4 / 2.2 ms |
| Qdrant | 97% | 0.890 | ok | 2.2 / 5.6 ms | 12.1 s | 11.6 / 29.2 ms |
| Chroma | 97% | 0.890 | ok | 1.2 / 2.9 ms | 36.8 s | 3.1 / 5.4 ms |

Pinecone was skipped: no API key set.

**What this shows.** Identical recall and MRR across stores, as expected: with the same embeddings and a small corpus, retrieval quality comes from the embeddings and the chunking, not the database. 34 of the 35 questions found a relevant section in the top 5 in every store.

**What it does not show yet.** These are untuned defaults. Before drawing conclusions:
- pgvector's slow write comes from inserting row by row into a table that already has an HNSW index. Loading first and building the index afterwards (or using `COPY`) should be much faster.
- Qdrant builds its HNSW index in the background once a collection passes its indexing threshold; queries straight after a bulk write may hit an unindexed segment. Re-run the query step after `optimizer_status` is ok.
- Latency includes the HTTP or Postgres round trip from the host into Docker; all three run on one laptop.
- Pinecone is remote, so its latency includes internet round trips. Compare it on recall, filters and operations, not raw latency.
