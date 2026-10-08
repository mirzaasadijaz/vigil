"""query -> top-k relevant chunks, with a similarity floor so weak matches return nothing
(better to say "not in the documents" than to feed Claude an irrelevant paragraph)."""
from __future__ import annotations

import asyncio

from backend.core.config import settings
from backend.rag.stores import get_store
from backend.rag.stores.base import Hit, VectorStore


async def search(query: str, *, k: int | None = None, scope: str = "all",
                 category: str | None = None, min_score: float | None = None,
                 store: VectorStore | None = None) -> list[Hit]:
    store = store or get_store()
    k = max(1, min(int(k or getattr(settings, "RAG_TOP_K", 4)), 10))
    floor = getattr(settings, "RAG_MIN_SCORE", 0.2) if min_score is None else min_score

    where: dict = {}
    if scope == "documents":
        where["kind"] = "document"
    elif scope == "alerts":
        where["kind"] = "alert"
    if category:
        where["category"] = category

    hits = await asyncio.to_thread(store.search, query, k, where or None)
    return [h for h in hits if h.score >= floor]
