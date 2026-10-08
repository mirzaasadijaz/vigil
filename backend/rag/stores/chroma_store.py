"""Chroma persistent store (local files in data/rag/index). Synchronous - call via asyncio.to_thread."""
from __future__ import annotations

from pathlib import Path

import chromadb
from chromadb.config import Settings as ChromaSettings

from backend.rag import INDEX_DIR
from backend.rag.embedder import get_embedding_function
from backend.rag.stores.base import Chunk, Hit, VectorStore

_BATCH = 64


def _clean_meta(meta: dict) -> dict:
    """Chroma only accepts str/int/float/bool values - drop None, stringify the rest."""
    out = {}
    for k, v in (meta or {}).items():
        if v is None:
            continue
        out[k] = v if isinstance(v, (str, int, float, bool)) else str(v)
    return out


def _to_where(where: dict | None) -> dict | None:
    if not where:
        return None
    if len(where) == 1:
        return dict(where)
    return {"$and": [{k: v} for k, v in where.items()]}


class ChromaStore(VectorStore):
    def __init__(self, path: str | Path | None = None, collection: str = "vigil_knowledge",
                 embedding_function=None):
        self._path = Path(path or INDEX_DIR)
        self._path.mkdir(parents=True, exist_ok=True)
        self._name = collection
        self._ef = embedding_function or get_embedding_function()
        self._client = chromadb.PersistentClient(
            path=str(self._path), settings=ChromaSettings(anonymized_telemetry=False))
        self._col = self._open()

    def _open(self):
        return self._client.get_or_create_collection(
            name=self._name, embedding_function=self._ef,
            metadata={"hnsw:space": "cosine"})

    def upsert(self, chunks: list[Chunk]) -> None:
        for i in range(0, len(chunks), _BATCH):
            part = chunks[i:i + _BATCH]
            self._col.upsert(
                ids=[c.id for c in part],
                documents=[c.text for c in part],
                metadatas=[_clean_meta(c.metadata) or {"_": ""} for c in part],
            )

    def search(self, query: str, k: int = 4, where: dict | None = None) -> list[Hit]:
        n = self._col.count()
        if n == 0:
            return []
        res = self._col.query(query_texts=[query], n_results=min(k, n),
                              where=_to_where(where),
                              include=["documents", "metadatas", "distances"])
        hits = []
        for id_, doc, meta, dist in zip(res["ids"][0], res["documents"][0],
                                        res["metadatas"][0], res["distances"][0]):
            hits.append(Hit(id=id_, text=doc, metadata=meta or {},
                            score=max(0.0, min(1.0, 1.0 - float(dist)))))
        return hits

    def delete(self, where: dict | None = None, ids: list[str] | None = None) -> None:
        if ids:
            self._col.delete(ids=ids)
        elif where:
            self._col.delete(where=_to_where(where))

    def count(self) -> int:
        return self._col.count()

    def reset(self) -> None:
        try:
            self._client.delete_collection(self._name)
        except Exception:
            pass
        self._col = self._open()
