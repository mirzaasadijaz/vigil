"""RAG package. Central place for on-disk locations (all under <project>/data/rag)."""
from pathlib import Path

from backend.core.config import settings

DATA_DIR = Path(settings.BASE_DIR) / "data" / "rag"
DOCS_DIR = DATA_DIR / "documents"      # put SOPs / policies / manuals here
INDEX_DIR = DATA_DIR / "index"         # Chroma persistent files (git-ignored)
CACHE_DIR = DATA_DIR / "cache"         # ingest state files (git-ignored)

for _d in (DOCS_DIR, INDEX_DIR, CACHE_DIR):
    _d.mkdir(parents=True, exist_ok=True)
