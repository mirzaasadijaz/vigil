"""Run from project root:  pytest tests -q
Uses a fake deterministic embedder, so no model download and no DB."""
import asyncio
import hashlib
import re

import pytest
from chromadb.api.types import EmbeddingFunction

from backend.rag import retriever
from backend.rag.chunker import chunk_text
from backend.rag.stores.chroma_store import ChromaStore


class FakeEF(EmbeddingFunction):
    """Bag-of-words hashed into 1024 dims: texts sharing words are close."""
    def __init__(self): pass

    def __call__(self, input):
        out = []
        for t in input:
            v = [0.0] * 1024
            for w in re.findall(r"[a-z]+", t.lower()):
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 1024] += 1.0
            out.append(v)
        return out

    @staticmethod
    def name(): return "fake-bow"
    def get_config(self): return {}
    @staticmethod
    def build_from_config(config): return FakeEF()


@pytest.fixture
def store(tmp_path):
    return ChromaStore(path=tmp_path / "idx", collection="test_col", embedding_function=FakeEF())


def _add(store, source, text, **meta):
    store.upsert(chunk_text(text, source, meta={"kind": "document", "category": "sop", **meta}))


def test_chunker_respects_size_and_overlap():
    text = "\n\n".join(f"Paragraph {i}. " + "word " * 60 for i in range(10))
    chunks = chunk_text(text, "a.md", size=500, overlap=80)
    assert len(chunks) > 3
    assert all(len(c.text) <= 500 + 80 + 2 for c in chunks)
    assert [c.metadata["chunk_index"] for c in chunks] == list(range(len(chunks)))
    assert chunks[0].id == "a.md#0"


def test_search_finds_relevant_and_applies_floor(store):
    _add(store, "sop/intrusion.md", "intrusion alert restricted zone call security supervisor")
    _add(store, "sop/fire.md", "fire evacuation assembly point extinguisher")
    hits = asyncio.run(retriever.search("intrusion alert zone", store=store, min_score=0.1))
    assert hits and hits[0].metadata["source"] == "sop/intrusion.md"
    # unrelated query -> nothing passes a sensible floor
    assert asyncio.run(retriever.search("zzz qqq xxx", store=store, min_score=0.5)) == []


def test_scope_and_reupsert_is_idempotent(store):
    _add(store, "d.md", "camera maintenance schedule")
    store.upsert(chunk_text("operator said reflection on glass", "alert #1",
                            meta={"kind": "alert", "category": "alert"}))
    only_docs = asyncio.run(retriever.search("camera reflection", scope="documents",
                                             store=store, min_score=0.0))
    assert {h.metadata["kind"] for h in only_docs} == {"document"}
    n = store.count()
    _add(store, "d.md", "camera maintenance schedule")        # same id again
    assert store.count() == n


def test_delete_by_source_and_reset(store):
    _add(store, "x.md", "alpha beta")
    _add(store, "y.md", "gamma delta")
    store.delete(where={"kind": "document", "source": "x.md"})
    assert store.count() == 1
    store.reset()
    assert store.count() == 0
