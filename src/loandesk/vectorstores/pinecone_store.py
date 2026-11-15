from __future__ import annotations

import time

from pinecone import Pinecone, ServerlessSpec

from loandesk.vectorstores.base import Chunk, Hit, VectorStore, batched


class PineconeStore(VectorStore):
    """Pinecone serverless. Managed and remote, and eventually consistent: a write is not
    searchable straight away, so reset and upsert wait until the count catches up."""

    name = "pinecone"

    def __init__(self, api_key: str, collection: str, cloud: str = "aws",
                 region: str = "us-east-1"):
        super().__init__(collection)
        if not api_key:
            raise RuntimeError("LOANDESK_PINECONE_API_KEY is not set")
        self.pc = Pinecone(api_key=api_key)
        self.index_name = f"loandesk-{collection.replace('_', '-')}"
        self.spec = ServerlessSpec(cloud=cloud, region=region)
        self._index = None

    @property
    def index(self):
        if self._index is None:
            self._index = self.pc.Index(self.index_name)
        return self._index

    def reset(self, dim: int) -> None:
        if self.pc.has_index(self.index_name):
            if self.pc.describe_index(self.index_name).dimension == dim:
                try:
                    self.index.delete(delete_all=True)
                except Exception:  # noqa: BLE001 - deleting an empty namespace raises
                    pass
                self._wait_for_count(0)
                return
            self.pc.delete_index(self.index_name)
        self.pc.create_index(self.index_name, dimension=dim, metric="cosine", spec=self.spec)
        while not self.pc.describe_index(self.index_name).status["ready"]:
            time.sleep(1)
        self._index = None

    def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        before = self.count()
        records = [
            {"id": c.id, "values": v, "metadata": {**c.metadata, "text": c.text}}
            for c, v in zip(chunks, vectors, strict=True)
        ]
        for batch in batched(records, 100):
            self.index.upsert(vectors=batch)
        self._wait_for_count(before + len(records))

    def _wait_for_count(self, expected: int, timeout_s: float = 120) -> None:
        deadline = time.time() + timeout_s
        while (self.count() > 0) if expected == 0 else (self.count() < expected):
            if time.time() > deadline:
                raise TimeoutError(f"Pinecone count did not reach {expected}")
            time.sleep(1)

    def search(self, vector, k=5, filters=None) -> list[Hit]:
        flt = {key: {"$eq": value} for key, value in filters.items()} if filters else None
        res = self.index.query(vector=vector, top_k=k, include_metadata=True, filter=flt)
        hits = []
        for m in res.matches:
            meta = dict(m.metadata or {})
            text = meta.pop("text", "")
            hits.append(Hit(id=m.id, text=text, score=float(m.score), metadata=meta))
        return hits

    def count(self) -> int:
        return int(self.index.describe_index_stats().total_vector_count)
