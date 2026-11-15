from __future__ import annotations

import json
import re

import numpy as np
import psycopg
from pgvector.psycopg import register_vector

from loandesk.vectorstores.base import Chunk, Hit, VectorStore, batched


class PgVectorStore(VectorStore):
    """Postgres + pgvector. One table per collection, HNSW index on cosine distance."""

    name = "pgvector"

    def __init__(self, database_url: str, collection: str):
        super().__init__(collection)
        if not re.fullmatch(r"[a-z0-9_]+", collection):
            raise ValueError("collection must be lowercase letters, digits and underscores")
        self.table = f"kb_{collection}"
        self.conn = psycopg.connect(database_url.replace("+psycopg", ""), autocommit=True)
        self.conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        register_vector(self.conn)

    def reset(self, dim: int) -> None:
        self.conn.execute(f"DROP TABLE IF EXISTS {self.table}")
        self.conn.execute(
            f"CREATE TABLE {self.table} (id text PRIMARY KEY, text text NOT NULL, "
            f"metadata jsonb NOT NULL DEFAULT '{{}}', embedding vector({dim}) NOT NULL)"
        )
        self.conn.execute(
            f"CREATE INDEX ON {self.table} USING hnsw (embedding vector_cosine_ops)"
        )
        self.conn.execute(f"CREATE INDEX ON {self.table} USING gin (metadata)")

    def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        rows = [(c.id, c.text, json.dumps(c.metadata), np.asarray(v, dtype=np.float32))
                for c, v in zip(chunks, vectors, strict=True)]
        with self.conn.cursor() as cur:
            for batch in batched(rows, 500):
                cur.executemany(
                    f"INSERT INTO {self.table} (id, text, metadata, embedding) "
                    "VALUES (%s, %s, %s, %s) ON CONFLICT (id) DO UPDATE SET "
                    "text = EXCLUDED.text, metadata = EXCLUDED.metadata, "
                    "embedding = EXCLUDED.embedding",
                    batch,
                )

    def search(self, vector, k=5, filters=None) -> list[Hit]:
        vec = np.asarray(vector, dtype=np.float32)
        where, params = "", [vec, vec, k]
        if filters:
            where, params = "WHERE metadata @> %s::jsonb", [vec, json.dumps(filters), vec, k]
        rows = self.conn.execute(
            f"SELECT id, text, metadata, 1 - (embedding <=> %s) AS score FROM {self.table} "
            f"{where} ORDER BY embedding <=> %s LIMIT %s",
            params,
        ).fetchall()
        return [Hit(id=r[0], text=r[1], metadata=r[2], score=float(r[3])) for r in rows]

    def count(self) -> int:
        return self.conn.execute(f"SELECT count(*) FROM {self.table}").fetchone()[0]

    def close(self) -> None:
        self.conn.close()
