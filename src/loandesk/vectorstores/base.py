"""The interface every vector database adapter implements.

Four operations, cosine similarity, equality filters on metadata. Scores are
similarities in [−1, 1], higher is better, whatever the database reports natively.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class Chunk:
    id: str
    text: str
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass
class Hit:
    id: str
    text: str
    score: float
    metadata: dict[str, str] = field(default_factory=dict)


class VectorStore(ABC):
    name: str

    def __init__(self, collection: str):
        self.collection = collection

    @abstractmethod
    def reset(self, dim: int) -> None:
        """Drop the collection if it exists and create it empty."""

    @abstractmethod
    def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None: ...

    @abstractmethod
    def search(self, vector: list[float], k: int = 5,
               filters: dict[str, str] | None = None) -> list[Hit]: ...

    @abstractmethod
    def count(self) -> int: ...

    def close(self) -> None:  # noqa: B027 - optional hook
        pass


def batched(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]
