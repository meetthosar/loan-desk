from __future__ import annotations

import uuid

from qdrant_client import QdrantClient
from qdrant_client import models as qm

from loandesk.vectorstores.base import Chunk, Hit, VectorStore, batched


class QdrantStore(VectorStore):
    """Qdrant. Point ids must be UUIDs or integers, so chunk ids are mapped with uuid5."""

    name = "qdrant"

    def __init__(self, url: str, collection: str):
        super().__init__(collection)
        self.client = QdrantClient(url=url) if url != "memory" else QdrantClient(":memory:")

    @staticmethod
    def _point_id(chunk_id: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id))

    def reset(self, dim: int) -> None:
        if self.client.collection_exists(self.collection):
            self.client.delete_collection(self.collection)
        self.client.create_collection(
            self.collection,
            vectors_config=qm.VectorParams(size=dim, distance=qm.Distance.COSINE),
        )

    def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        points = [
            qm.PointStruct(id=self._point_id(c.id), vector=v,
                           payload={"chunk_id": c.id, "text": c.text, **c.metadata})
            for c, v in zip(chunks, vectors, strict=True)
        ]
        for batch in batched(points, 256):
            self.client.upsert(self.collection, points=batch, wait=True)

    def search(self, vector, k=5, filters=None) -> list[Hit]:
        query_filter = None
        if filters:
            query_filter = qm.Filter(must=[
                qm.FieldCondition(key=key, match=qm.MatchValue(value=value))
                for key, value in filters.items()
            ])
        points = self.client.query_points(
            self.collection, query=vector, query_filter=query_filter, limit=k, with_payload=True
        ).points
        hits = []
        for p in points:
            payload = dict(p.payload or {})
            chunk_id, text = payload.pop("chunk_id"), payload.pop("text")
            hits.append(Hit(id=chunk_id, text=text, score=float(p.score), metadata=payload))
        return hits

    def count(self) -> int:
        return self.client.count(self.collection, exact=True).count

    def close(self) -> None:
        self.client.close()
