"""Local embeddings with fastembed (ONNX, no GPU, no API cost).

Kept fixed while comparing vector databases, so the comparison measures the database.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np
from fastembed import TextEmbedding


class Embedder:
    def __init__(self, model_name: str):
        self.model_name = model_name
        self._model = TextEmbedding(model_name=model_name)
        self.dim = len(next(iter(self._model.embed(["probe"]))))

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [_unit(v) for v in self._model.passage_embed(texts)]

    def embed_query(self, text: str) -> list[float]:
        return _unit(next(iter(self._model.query_embed([text]))))


def _unit(v: np.ndarray) -> list[float]:
    v = np.asarray(v, dtype=np.float32)
    return (v / (np.linalg.norm(v) or 1.0)).tolist()


@lru_cache
def get_embedder(model_name: str) -> Embedder:
    return Embedder(model_name)
