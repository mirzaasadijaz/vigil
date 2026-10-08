from functools import lru_cache

from backend.rag.stores.base import Chunk, Hit, VectorStore


@lru_cache(maxsize=1)
def get_store() -> VectorStore:
    """One process-wide store. Swap the class here to change backend."""
    from backend.rag.stores.chroma_store import ChromaStore
    return ChromaStore()


__all__ = ["Chunk", "Hit", "VectorStore", "get_store"]
