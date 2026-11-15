from __future__ import annotations

import chromadb

from loandesk.vectorstores.base import Chunk, Hit, VectorStore, batched


class ChromaStore(VectorStore):
    """Chroma. Reports cosine distance, converted here to similarity (1 − distance)."""

    name = "chroma"

    def __init__(self, url: str, collection: str):
        super().__init__(collection)
        if url == "memory":
            self.client = chromadb.EphemeralClient()
        else:
            host, _, port = url.partition(":")
            self.client = chromadb.HttpClient(host=host, port=int(port or 8000))
        self._col = None

    @property
    def col(self):
        if self._col is None:
            self._col = self.client.get_collection(self.collection)
        return self._col

    def reset(self, dim: int) -> None:
        try:
            self.client.delete_collection(self.collection)
        except Exception:  # noqa: BLE001 - Chroma raises different errors for "not found"
            pass
        self._col = self.client.create_collection(
            self.collection, configuration={"hnsw": {"space": "cosine"}}
        )

    def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        pairs = list(zip(chunks, vectors, strict=True))
        for batch in batched(pairs, 1000):
            self.col.upsert(
                ids=[c.id for c, _ in batch],
                embeddings=[v for _, v in batch],
                documents=[c.text for c, _ in batch],
                metadatas=[c.metadata or {"_": ""} for c, _ in batch],
            )

    def search(self, vector, k=5, filters=None) -> list[Hit]:
        where = None
        if filters:
            clauses = [{key: {"$eq": value}} for key, value in filters.items()]
            where = clauses[0] if len(clauses) == 1 else {"$and": clauses}
        res = self.col.query(query_embeddings=[vector], n_results=k, where=where)
        return [
            Hit(id=i, text=doc, score=1.0 - float(dist), metadata=dict(meta or {}))
            for i, doc, dist, meta in zip(
                res["ids"][0], res["documents"][0], res["distances"][0], res["metadatas"][0],
                strict=True,
            )
        ]

    def count(self) -> int:
        return self.col.count()
