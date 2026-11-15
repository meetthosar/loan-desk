"""Vector store adapters. Import cost is kept low: each client loads only when its store is used."""

from __future__ import annotations

from loandesk.config import Settings
from loandesk.vectorstores.base import Chunk, Hit, VectorStore

STORES = ("pgvector", "qdrant", "chroma", "pinecone")


def get_store(name: str, settings: Settings, collection: str = "policies") -> VectorStore:
    if name == "pgvector":
        from loandesk.vectorstores.pgvector_store import PgVectorStore

        return PgVectorStore(settings.database_url, collection)
    if name == "qdrant":
        from loandesk.vectorstores.qdrant_store import QdrantStore

        return QdrantStore(settings.qdrant_url, collection)
    if name == "chroma":
        from loandesk.vectorstores.chroma_store import ChromaStore

        return ChromaStore(settings.chroma_url, collection)
    if name == "pinecone":
        from loandesk.vectorstores.pinecone_store import PineconeStore

        return PineconeStore(settings.pinecone_api_key, collection, settings.pinecone_cloud,
                             settings.pinecone_region)
    raise ValueError(f"Unknown vector store {name!r}; choose from {', '.join(STORES)}")


__all__ = ["Chunk", "Hit", "VectorStore", "STORES", "get_store"]
