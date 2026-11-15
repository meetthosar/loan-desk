from __future__ import annotations

import time
from pathlib import Path

from loandesk.config import ROOT
from loandesk.embeddings import Embedder
from loandesk.knowledge.chunking import load_policies
from loandesk.vectorstores.base import Hit, VectorStore

POLICIES_DIR = ROOT / "data" / "policies"


class Retriever:
    def __init__(self, store: VectorStore, embedder: Embedder, k: int = 4):
        self.store = store
        self.embedder = embedder
        self.k = k

    def search(self, query: str, k: int | None = None,
               filters: dict[str, str] | None = None) -> list[Hit]:
        return self.store.search(self.embedder.embed_query(query), k or self.k, filters)


def ingest(store: VectorStore, embedder: Embedder,
           directory: Path = POLICIES_DIR) -> dict[str, float]:
    chunks = load_policies(directory)
    start = time.perf_counter()
    vectors = embedder.embed_documents([c.text for c in chunks])
    embed_s = time.perf_counter() - start
    start = time.perf_counter()
    store.reset(embedder.dim)
    store.upsert(chunks, vectors)
    return {"chunks": len(chunks), "embed_s": embed_s, "write_s": time.perf_counter() - start}
