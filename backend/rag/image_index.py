"""CLIP index over alert snapshots: find alerts by what is IN the picture.

Design notes (what changed vs. the first version and why):
  * Paths are normalised to forward slashes - os.path.join() on Windows stores
    "snapshots\\a_x.jpg", which a Linux/Docker backend cannot open.
  * "Not yet indexed" is decided per candidate id (cheap) instead of fetching every id in the
    index, and the candidate window is the newest WINDOW alerts - so a burst of more than
    `limit` new snapshots is caught up on the next call instead of being skipped forever.
  * Images are decoded at thumbnail size and encoded in small batches: bounded memory.
  * Search drops hits whose snapshot file was deleted (retention clean-up) and can apply a
    similarity floor (RAG_IMAGE_MIN_SCORE; CLIP text->image scores are NOT on the same scale
    as the text retriever's, calibrate on your own snapshots).
"""
from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import chromadb
from chromadb.config import Settings as ChromaSettings
from PIL import Image
from sqlalchemy import select

from backend.core.config import settings
from backend.core.database import AsyncSessionLocal
from backend.models.alert import AlertLog
from backend.rag import INDEX_DIR

TZ = ZoneInfo("Asia/Karachi")
COLLECTION = "vigil_snapshots"
WINDOW = 2000          # newest snapshot-bearing alerts considered by a catch-up pass
BATCH = 16             # images decoded + encoded at a time (memory bound)
THUMB = 448            # CLIP resizes to 224 anyway; decode no bigger than this

_model = None
_col = None
_init_lock = threading.Lock()          # _clip()/_collection() run in worker threads
_index_lock = asyncio.Lock()           # one indexing pass at a time
_last_catchup = 0.0
_bad: set[str] = set()                 # ids whose file exists but cannot be decoded


# ── paths ────────────────────────────────────────────────────────────────────
def _rel(snapshot: str) -> str:
    """DB value -> forward-slash relative path (works for rows written on Windows)."""
    return str(snapshot).replace("\\", "/").lstrip("/")


def _abs(rel: str) -> Path:
    return Path(settings.MEDIA_ROOT) / rel


# ── lazy singletons ──────────────────────────────────────────────────────────
def _clip():
    global _model
    with _init_lock:
        if _model is None:
            from sentence_transformers import SentenceTransformer
            _model = SentenceTransformer("clip-ViT-B-32")
        return _model


def _client():
    return chromadb.PersistentClient(
        path=str(INDEX_DIR.parent / "index_images"),
        settings=ChromaSettings(anonymized_telemetry=False))


def _collection():
    global _col
    with _init_lock:
        if _col is None:
            _col = _client().get_or_create_collection(
                COLLECTION, metadata={"hnsw:space": "cosine"})
        return _col


def _reset_sync() -> None:
    global _col
    with _init_lock:
        try:
            _client().delete_collection(COLLECTION)
        except Exception:
            pass
        _col = None
        _bad.clear()


async def reset_index() -> None:
    """Wipe the image index (the next index_new / backfill rebuilds it)."""
    await asyncio.to_thread(_reset_sync)


def count() -> int:
    return _collection().count()


# ── indexing ─────────────────────────────────────────────────────────────────
def _load(path: Path) -> Image.Image:
    with Image.open(path) as im:
        im.draft("RGB", (THUMB, THUMB))        # JPEG: decode at reduced size (fast, small)
        small = im.convert("RGB")
    small.thumbnail((THUMB, THUMB))
    return small


def _index_sync(rows: list[dict]) -> int:
    col, model, done = _collection(), _clip(), 0
    for i in range(0, len(rows), BATCH):
        imgs, keep = [], []
        for r in rows[i:i + BATCH]:
            try:
                imgs.append(_load(_abs(r["snapshot"])))
                keep.append(r)
            except Exception:
                _bad.add(str(r["id"]))         # don't retry a corrupt file on every pass
        if not keep:
            continue
        vecs = model.encode(imgs, batch_size=BATCH, normalize_embeddings=True).tolist()
        col.upsert(
            ids=[str(r["id"]) for r in keep], embeddings=vecs,
            metadatas=[{"alert_id": r["id"], "ts": r["ts"], "camera_id": r["camera_id"] or 0,
                        "unknown": bool(r["unknown"]), "snapshot": r["snapshot"]}
                       for r in keep])
        done += len(keep)
    return done


def _select_todo(rows: list[dict], limit: int) -> list[dict]:
    """Newest-first rows that are not indexed, not known-bad, and whose file exists."""
    col, ids = _collection(), [str(r["id"]) for r in rows]
    have: set[str] = set()
    for i in range(0, len(ids), 500):
        have.update(col.get(ids=ids[i:i + 500], include=[])["ids"])
    todo = []
    for r in rows:
        if str(r["id"]) in have or str(r["id"]) in _bad or not _abs(r["snapshot"]).is_file():
            continue
        todo.append(r)
        if len(todo) >= limit:
            break
    return todo


async def _candidates(window: int) -> list[dict]:
    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(AlertLog.id, AlertLog.snapshot, AlertLog.camera_id,
                   AlertLog.unknown_person, AlertLog.timestamp)
            .where(AlertLog.snapshot.is_not(None), AlertLog.snapshot != "")
            .order_by(AlertLog.id.desc()).limit(window))
        return [{"id": r.id, "snapshot": _rel(r.snapshot), "camera_id": r.camera_id,
                 "unknown": r.unknown_person, "ts": int(r.timestamp.timestamp())}
                for r in res.all() if r.timestamp is not None]


async def index_new(limit: int = 100, window: int = WINDOW, throttle: float = 0.0) -> int:
    """Index up to `limit` not-yet-indexed snapshots among the newest `window` alerts.

    throttle: skip the pass entirely if one ran less than this many seconds ago (used on the
    search path so a chatty user does not hit the DB + disk on every question)."""
    global _last_catchup
    if throttle and time.monotonic() - _last_catchup < throttle:
        return 0
    async with _index_lock:
        rows = await _candidates(window)
        todo = await asyncio.to_thread(_select_todo, rows, limit)
        n = await asyncio.to_thread(_index_sync, todo) if todo else 0
        _last_catchup = time.monotonic()
        return n


async def backfill(window: int = 1_000_000, chunk: int = 200) -> int:
    """Index everything that is missing (run from the CLI, not from a chat request)."""
    total = 0
    while True:
        n = await index_new(limit=chunk, window=window)
        total += n
        if n == 0:
            return total


# ── search ───────────────────────────────────────────────────────────────────
def _search_sync(query, k, unknown_only, camera_id, t_from, t_to, min_score):
    where = []
    if unknown_only:
        where.append({"unknown": True})
    if camera_id:
        where.append({"camera_id": int(camera_id)})
    if t_from:
        where.append({"ts": {"$gte": t_from}})
    if t_to:
        where.append({"ts": {"$lte": t_to}})
    col = _collection()
    n = col.count()
    if n == 0:
        return []
    vec = _clip().encode([query], normalize_embeddings=True).tolist()
    res = col.query(query_embeddings=vec, n_results=min(max(k * 3, k + 10), n),
                    where=({"$and": where} if len(where) > 1 else where[0] if where else None),
                    include=["metadatas", "distances"])
    out = []
    for m, d in zip(res["metadatas"][0], res["distances"][0]):
        score = 1.0 - d
        if score < min_score or not _abs(_rel(m["snapshot"])).is_file():
            continue                                   # below floor / file was cleaned up
        out.append((m, score))
        if len(out) == k:
            break
    return out


def _day(s, end=False):
    if not s:
        return None
    d = datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=TZ)
    return int(d.timestamp()) + (86399 if end else 0)


async def search(query: str, k: int = 5, unknown_only: bool = False, camera_id=None,
                 date_from: str | None = None, date_to: str | None = None,
                 min_score: float | None = None):
    floor = float(getattr(settings, "RAG_IMAGE_MIN_SCORE", 0.0)) if min_score is None else min_score
    return await asyncio.to_thread(_search_sync, query, k, unknown_only, camera_id,
                                   _day(date_from), _day(date_to, end=True), floor)