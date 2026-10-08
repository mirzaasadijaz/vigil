"""Text -> vector. Runs LOCALLY (no API, no data leaves the machine).

RAG_EMBED_MODEL in config/.env:
  "default"  -> Chroma's built-in ONNX all-MiniLM-L6-v2 (small, fast, English-centric;
                downloads ~80 MB once on first use)
  anything else -> a sentence-transformers model name, e.g.
                "paraphrase-multilingual-MiniLM-L12-v2" (better for Urdu / Roman Urdu docs;
                needs `pip install sentence-transformers`, torch is already in this project)

NOTE: changing the model changes the vector space. Run a full reindex afterwards.
"""
from functools import lru_cache

from backend.core.config import settings


@lru_cache(maxsize=1)
def get_embedding_function():
    name = getattr(settings, "RAG_EMBED_MODEL", "default") or "default"
    if name == "default":
        from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
        return DefaultEmbeddingFunction()
    from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
    return SentenceTransformerEmbeddingFunction(model_name=name)
