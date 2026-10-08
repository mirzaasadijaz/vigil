"""
Employee embedding store — InsightFace (SCRFD + ArcFace, ONNX), multi-embedding gallery.

Each employee has a **gallery** of 512-d L2-normalized ArcFace vectors, not just one:
  - the *manual anchor* — encoded once from their enrollment photo (media/employees/…);
    it is permanent and never pruned or outvoted.
  - up to a few *auto* vectors — captured at runtime from high-confidence live matches
    (different angles/lighting), seeded back in from the DB on startup. These lift
    recall on poor camera streams *without* tightening the runtime match threshold.

The gallery is keyed by the anchor photo's basename (the identity key that flows
through the whole perception pipeline). Runtime recognition is: detect faces -> embed
-> cosine match vs every employee's best vector. `find_match` also reports the *margin*
to the second-best employee, which the auto-enrollment gate uses to avoid poisoning the
gallery — runtime labeling itself stays lenient (unchanged threshold).

Driven from the perception child process (single GPU owner).
"""
import os
import threading
import cv2
import numpy as np

from backend.core.config import settings
from backend.services.detectors import face as face_det

# key (anchor photo basename) -> list of gallery entries {vec, source, quality}
_gallery: dict[str, list[dict]] = {}
_photo_map: dict[str, str] = {}      # key -> relative path of the anchor photo (employees/foo.jpg)
_lock = threading.RLock()

MAX_AUTO = 8                          # cap on auto vectors per employee (anchor excluded)


def load_all_embeddings() -> None:
    db_path = os.path.join(settings.MEDIA_ROOT, "employees")
    if not os.path.exists(db_path):
        print("[EMBED] employees/ directory not found — skipping preload.")
        return
    all_files = [f for f in os.listdir(db_path) if f.lower().endswith((".jpg", ".jpeg", ".png"))]
    main = [f for f in all_files if "_extra" not in f]
    extra = [f for f in all_files if "_extra" in f]

    print(f"[EMBED] Preloading {len(main)} employee embeddings + {len(extra)} extra angles…", flush=True)
    loaded = 0
    for fname in main:
        if _encode_and_store(os.path.join(db_path, fname), f"employees/{fname}"):
            loaded += 1

    # group extra photos under their matching main anchor
    extra_loaded = 0
    for fname in extra:
        prefix = fname.split("_extra", 1)[0]
        match = [k for k in _gallery if k.startswith(prefix)]
        if not match:
            print(f"[EMBED] No main anchor found for extra {fname} (prefix={prefix}) — skipping.")
            continue
        key = match[0]
        full = os.path.join(db_path, fname)
        img = cv2.imread(full)
        if img is None:
            continue
        emb = face_det.embed_image(img)
        if emb is None:
            print(f"[EMBED] No face detected in extra {fname} — skipping.")
            continue
        with _lock:
            _gallery.setdefault(key, []).append(
                {"vec": np.asarray(emb, dtype=np.float32), "source": "manual_extra", "quality": 1.0})
        extra_loaded += 1
    print(f"[EMBED] Done — {loaded} anchors + {extra_loaded} extra angles cached.", flush=True)


def load_photos(rel_paths: "list[str]") -> None:
    """Rebuild the gallery from EXACTLY the given employee photos (rel paths under
    MEDIA_ROOT, e.g. 'employees/foo.jpg'). Unlike load_all_embeddings() this never scans
    the directory, so a stray/orphan/duplicate file in media/employees/ that is not part
    of the HR avatar index can no longer become a phantom identity (the root cause of the
    earlier mis-identification). Clears every anchor + auto vector; the caller re-seeds autos."""
    with _lock:
        _gallery.clear()
        _photo_map.clear()
    ok = 0
    for rel in rel_paths:
        if rel and _encode_and_store(os.path.join(settings.MEDIA_ROOT, rel), rel):
            ok += 1
    print(f"[EMBED] Loaded {ok}/{len(rel_paths)} HR avatar anchors (index-referenced only).", flush=True)

    # load extra angles that match any loaded anchor
    db_path = os.path.join(settings.MEDIA_ROOT, "employees")
    if not os.path.exists(db_path):
        return
    extra = [f for f in os.listdir(db_path) if f.lower().endswith((".jpg", ".jpeg", ".png")) and "_extra" in f]
    if not extra:
        return
    extra_loaded = 0
    for fname in extra:
        prefix = fname.split("_extra", 1)[0]
        with _lock:
            match = [k for k in _gallery if k.startswith(prefix)]
        if not match:
            continue
        key = match[0]
        full = os.path.join(db_path, fname)
        img = cv2.imread(full)
        if img is None:
            continue
        emb = face_det.embed_image(img)
        if emb is None:
            continue
        with _lock:
            _gallery.setdefault(key, []).append(
                {"vec": np.asarray(emb, dtype=np.float32), "source": "manual_extra", "quality": 1.0})
        extra_loaded += 1
    if extra_loaded:
        print(f"[EMBED] Loaded {extra_loaded} extra angle(s) from disk.", flush=True)


def add_photo(full_path: str, photo_rel: str) -> None:
    _encode_and_store(full_path, photo_rel)


def remove_photo(photo_rel: str) -> None:
    fname = os.path.basename(photo_rel)
    with _lock:
        _gallery.pop(fname, None)
        _photo_map.pop(fname, None)
    print(f"[EMBED] Removed: {fname}", flush=True)


def _encode_and_store(full_path: str, photo_rel: str) -> bool:
    """Encode an enrollment photo as the employee's permanent *manual anchor*. Any
    existing auto vectors under this key are preserved; the anchor slot is (re)set."""
    fname = os.path.basename(full_path)
    try:
        img = cv2.imread(full_path)
        if img is None:
            print(f"[EMBED] Could not read {fname}")
            return False
        emb = face_det.embed_image(img)
        if emb is None:
            print(f"[EMBED] No face detected in {fname} — skipping.")
            return False
        with _lock:
            entries = _gallery.setdefault(fname, [])
            # drop any prior anchor, keep auto vectors
            entries[:] = [e for e in entries if e["source"] != "manual"]
            entries.insert(0, {"vec": np.asarray(emb, dtype=np.float32), "source": "manual", "quality": 1.0})
            _photo_map[fname] = photo_rel
        print(f"[EMBED] Cached anchor: {fname}", flush=True)
        return True
    except Exception as e:
        print(f"[EMBED] Failed to encode {fname}: {e}", flush=True)
        return False


def add_manual_photos(primary_rel: str, extra_paths: list[str]) -> int:
    """Add ADDITIONAL manual reference images for an employee, on top of their
    primary anchor photo. Stored permanently under the SAME gallery key as the
    anchor (never pruned by the auto-enrollment cap), so recognition compares
    against every enrollment photo — not just the first one given."""
    key = os.path.basename(primary_rel)
    added = 0
    for path in extra_paths:
        img = cv2.imread(path)
        if img is None:
            print(f"[EMBED] Could not read {path}")
            continue
        emb = face_det.embed_image(img)
        if emb is None:
            print(f"[EMBED] No face detected in {path} — skipping.")
            continue
        with _lock:
            entries = _gallery.setdefault(key, [])
            entries.append({"vec": np.asarray(emb, dtype=np.float32),
                             "source": "manual_extra", "quality": 1.0})
        added += 1
    print(f"[EMBED] Added {added} extra manual reference(s) for {key}", flush=True)
    return added


# ── Runtime gallery growth (auto-enrollment) ─────────────────────────────────────
def seed_vectors(vecs: list[dict]) -> None:
    """Bulk-load persisted auto vectors at startup. Each item: {key, vec(bytes),
    source, quality}. `key` is the current anchor basename for that employee."""
    added = 0
    with _lock:
        for item in vecs:
            key = item.get("key")
            buf = item.get("vec")
            if not key or not buf:
                continue
            arr = np.frombuffer(buf, dtype=np.float32).copy()
            if arr.size != 512:
                continue
            _gallery.setdefault(key, []).append(
                {"vec": arr, "source": item.get("source", "auto"), "quality": item.get("quality")})
            added += 1
    print(f"[EMBED] Seeded {added} stored auto vectors across {len(set(v.get('key') for v in vecs))} employees.", flush=True)


def add_runtime_vector(key: str, vec: np.ndarray, source: str = "auto", quality: float | None = None) -> None:
    """Append a freshly captured vector to a gallery for *instant* use (the writer
    persists it in parallel). Enforces the auto cap by dropping the oldest auto vector."""
    with _lock:
        entries = _gallery.setdefault(key, [])
        entries.append({"vec": np.asarray(vec, dtype=np.float32), "source": source, "quality": quality})
        autos = [e for e in entries if e["source"] == "auto"]
        if len(autos) > MAX_AUTO:
            # drop the oldest auto (FIFO); never touch the manual anchor. Remove by
            # identity — these dicts hold numpy arrays, so list.remove()'s == compare
            # would raise "truth value of an array is ambiguous".
            oldest = autos[0]
            for i, e in enumerate(entries):
                if e is oldest:
                    del entries[i]
                    break


def auto_count(key: str) -> int:
    with _lock:
        return sum(1 for e in _gallery.get(key, []) if e["source"] == "auto")


def reset_auto(key: str) -> None:
    """Drop all auto-learned vectors for an employee, keeping the manual anchor."""
    with _lock:
        entries = _gallery.get(key)
        if entries:
            entries[:] = [e for e in entries if e["source"] in ("manual", "manual_extra")]
    print(f"[EMBED] Reset learned vectors for {key}", flush=True)


def is_diverse(key: str, vec: np.ndarray, dup_sim: float = 0.92) -> bool:
    """True if `vec` adds a genuinely new angle/appearance — i.e. it is NOT a near
    duplicate (cosine >= dup_sim) of any vector already in the gallery. Keeps the
    auto gallery diverse instead of stacking redundant near-identical frames."""
    v = np.asarray(vec, dtype=np.float32)
    with _lock:
        entries = list(_gallery.get(key, []))
    if not entries:
        return True
    mat = np.stack([e["vec"] for e in entries])
    return float(np.max(mat @ v)) < dup_sim


# ── Matching ─────────────────────────────────────────────────────────────────────
def find_match(query_vec: np.ndarray) -> tuple[str | None, float, float]:
    """Per-employee gallery cosine match.

    Returns (key, best_sim, margin):
      key      = anchor basename of the matched employee, or None if below threshold
      best_sim = highest cosine similarity to any of that employee's vectors
      margin   = best_sim minus the best similarity to the *next* employee

    Runtime labeling uses the lenient `face_det.MATCH_SIM` threshold (unchanged) so poor
    streams keep recognizing people; `margin` is surfaced only for the auto-enroll gate.
    """
    with _lock:
        if not _gallery:
            return None, 0.0, 0.0
        snapshot = {k: [e["vec"] for e in v] for k, v in _gallery.items() if v}
    if query_vec is None or not snapshot:
        return None, 0.0, 0.0
    best_per_emp: list[tuple[str, float]] = []
    for k, vecs in snapshot.items():
        mat = np.stack(vecs)                 # (Ni, 512), already L2-normed
        best_per_emp.append((k, float(np.max(mat @ query_vec))))
    best_per_emp.sort(key=lambda x: x[1], reverse=True)
    key, best = best_per_emp[0]
    second = best_per_emp[1][1] if len(best_per_emp) > 1 else 0.0
    margin = best - second
    if best >= face_det.MATCH_SIM:
        return key, best, margin
    return None, best, margin


def cache_size() -> int:
    with _lock:
        return sum(len(v) for v in _gallery.values())
