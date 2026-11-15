# Project structure (and how to reuse it)

```
loan-desk/
├── config/models.yaml          # every model slug, routing, caching, the experiment matrix
├── prompts/v1/                 # versioned prompts: system, router, judge
├── data/policies/              # knowledge base (markdown, one page per topic)
├── evals/
│   ├── cases/                  # seed.yaml (20) + extended.yaml (40) + policy.yaml
│   ├── retrieval/questions.yaml  # labelled questions for the vector DB benchmark
│   ├── messages.yaml           # intent map; also drives the simulation
│   ├── gate.yaml, labels.yaml  # release bar; hand labels for the judge
│   └── reports/                # generated
├── src/loandesk/
│   ├── config.py, wiring.py, tracing.py, cli.py
│   ├── llm/                    # OpenRouter client, spend ledger, price catalogue
│   ├── embeddings.py
│   ├── vectorstores/           # base interface + pgvector, qdrant, chroma, pinecone
│   ├── knowledge/              # chunking, ingest, retriever
│   ├── agent/                  # LangGraph graph, state, prompts, answer cache
│   ├── guardrails/             # PII vault, output checks
│   ├── evals/                  # case format, scorers, judge, runner, reports
│   ├── experiments/            # model matrix, vector bench, simulate, calibrate
│   ├── api/                    # FastAPI app, OIDC admin auth, KPIs, dashboard
│   ├── domain/                 # ← LoanDesk-specific: intents, loan maths
│   ├── tools/                  # ← LoanDesk-specific: 6 tools and their schemas
│   ├── db/                     # ← LoanDesk-specific: mock core banking + seed
│   └── integrations/           # ← LoanDesk-specific: Zendesk, OTP, payment webhook
├── tests/                      # offline: scripted LLM + hashing embedder
├── Dockerfile, docker-compose.yml, .github/workflows/ci.yml
└── docs/
```

## Starting the next project from this one

Copy as is (domain-free): `llm/`, `embeddings.py`, `vectorstores/`, `knowledge/`, `tracing.py`, `guardrails/pii.py`, `evals/` (runner, scorers, cases), `experiments/` (model_matrix, vector_bench, calibrate), `api/auth.py`, `api/kpis.py`, the Docker and CI files.

Replace (domain-specific): `domain/`, `tools/`, `db/`, `integrations/`, `prompts/`, `data/policies/`, `evals/cases/`, `evals/retrieval/questions.yaml`, and the intent-specific parts of `agent/graph.py` (the handoff rules in `route` and `ACCOUNT_INTENTS`).

If you plan several agents, the clean next step is to move the domain-free parts into a shared package (for example `agentkit/`) that both projects import, instead of copying.
