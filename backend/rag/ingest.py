"""documents + alert notes -> chunks -> vectors -> store.  Safe to run repeatedly (idempotent)."""
from __future__ import annotations

import asyncio
import json

from backend.rag import CACHE_DIR
from backend.rag.chunker import chunk_text
from backend.rag.sources import alerts_to_text
from backend.rag.sources.documents_loader import iter_documents
from backend.rag.stores import get_store
from backend.rag.stores.base import VectorStore

DOC_STATE = CACHE_DIR / "doc_hashes.json"
_lock = asyncio.Lock()          # one ingest at a time; embedding is CPU-heavy


def _load_hashes() -> dict:
    try:
        return json.loads(DOC_STATE.read_text())
    except Exception:
        return {}


def _ingest_documents_sync(store: VectorStore, full: bool) -> dict:
    old = {} if full else _load_hashes()
    new: dict[str, str] = {}
    stats = {"added_or_updated": 0, "unchanged": 0, "removed": 0, "chunks": 0}
    for doc in iter_documents():
        new[doc.source] = doc.sha1
        if old.get(doc.source) == doc.sha1:
            stats["unchanged"] += 1
            continue
        store.delete(where={"kind": "document", "source": doc.source})   # drop stale chunks
        chunks = chunk_text(doc.text, doc.source,
                            meta={"kind": "document", "category": doc.category})
        store.upsert(chunks)
        stats["added_or_updated"] += 1
        stats["chunks"] += len(chunks)
    for gone in set(old) - set(new):                                      # file deleted from disk
        store.delete(where={"kind": "document", "source": gone})
        stats["removed"] += 1
    DOC_STATE.write_text(json.dumps(new))
    return stats


async def ingest_documents(store: VectorStore | None = None, full: bool = False) -> dict:
    store = store or get_store()
    return await asyncio.to_thread(_ingest_documents_sync, store, full)


async def ingest_alerts(store: VectorStore | None = None, full: bool = False) -> dict:
    store = store or get_store()
    chunks, newest = await alerts_to_text.build_alert_chunks(full=full)
    if chunks:
        await asyncio.to_thread(store.upsert, chunks)
    if newest is not None:
        alerts_to_text.save_since(newest)
    return {"alert_chunks": len(chunks)}


async def reindex(full: bool = False) -> dict:
    """full=True wipes the index first (needed after changing the embedding model)."""
    async with _lock:
        store = get_store()
        if full:
            await asyncio.to_thread(store.reset)
            alerts_to_text.clear_state()
        result = {"documents": await ingest_documents(store, full)}
        try:
            result["alerts"] = await ingest_alerts(store, full)
        except Exception as e:                       # DB down must not lose the document ingest
            result["alerts"] = {"error": str(e)}
        result["total_chunks"] = await asyncio.to_thread(store.count)
        return result
