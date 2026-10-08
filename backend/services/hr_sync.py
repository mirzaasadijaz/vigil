"""
HR payroll sync — employees are sourced from the REMOTE HR HTTP API
(GET {HR_API_BASE}/api/employees), NOT from the database directly.

Each sync:
  1. Fetches every employee (employee_id, user_id, name, status, picture_url) from
     the HR API (paginated).
  2. Ensures each employee's avatar anchor exists in media/employees/ as
     <employee_id>_avatar.jpg — re-downloading from the Laravel storage URL when a
     new employee has no local anchor yet (existing anchors are kept untouched).
  3. Writes `media/_hr_index.json` — the identity map (employee_key <-> user_id) the
     perception writer uses to tag attendance / mess / alerts.
  4. Removes avatars of employees that vanished from HR and wipes their learned
     auto-vectors, so no ghost identity stays recognizable.
  5. Rebuilds the live perception gallery from the current avatars + stored vectors.
"""
import os

import requests

from sqlalchemy import create_engine, delete

from backend.core.config import settings
from backend.services import perception
from backend.services import hr_index

MEDIA_EMPLOYEES = os.path.join(settings.MEDIA_ROOT, "employees")
os.makedirs(MEDIA_EMPLOYEES, exist_ok=True)

TIMEOUT = (5, 20)


def _api_url(path: str) -> str:
    return f"{settings.HR_API_BASE.rstrip('/')}/{path.lstrip('/')}"


def fetch_hr_page(page: int = 1, per_page: int = 100) -> dict | None:
    """One page of employees from the remote HR API (GET /api/employees). Returns the
    Laravel envelope {"data": [...], "meta": {...}} or None on failure."""
    try:
        resp = requests.get(_api_url("/api/employees"),
                            params={"page": page, "per_page": per_page}, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[HR-SYNC] HR API page {page} fetch failed: {e}", flush=True)
        return None


def fetch_hr_employees():
    """Fetch every employee from the remote HR API (paginated). Returns a list of dicts
    {employee_id, user_id, name, status, picture_url}, or None on failure. The API's
    `id` is the remote users.id that the app writes into face_embeddings / alert_logs /
    mess_sessions / attendance_records."""
    items: list[dict] = []
    page = 1
    while True:
        body = fetch_hr_page(page)
        if body is None:
            return None
        rows = body.get("data") or []
        for e in rows:
            emp_id = str(e.get("employee_id") or "").strip()
            if not emp_id or e.get("id") is None:
                continue
            items.append({
                "employee_id": emp_id,
                "user_id": int(e["id"]),
                "name": (e.get("name") or "").strip(),
                "status": (e.get("status") or "active").strip().lower() or "active",
                "picture_url": e.get("picture_url"),
            })
        meta = body.get("meta") or {}
        cur = meta.get("current_page") or page
        last = meta.get("last_page") or cur
        if not rows or cur >= last:
            break
        page = cur + 1
    return items


def _download_avatar(url: str, employee_key: str) -> str | None:
    """Download the HR avatar into media/employees/. Returns the rel path or None."""
    if not url:
        return None
    rel = hr_index.avatar_rel(employee_key)
    full = hr_index.avatar_full(employee_key)
    try:
        resp = requests.get(url, timeout=TIMEOUT)
        resp.raise_for_status()
        if not resp.content:
            return None
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(resp.content)
        return rel
    except Exception as e:
        print(f"[HR-SYNC] avatar download failed for {employee_key}: {e}", flush=True)
        return None


def _ensure_avatar(url: str | None, employee_key: str) -> None:
    """Keep the existing local anchor (a changed image is picked up via notify on next
    sync); only download when a brand-new employee has no anchor on disk yet."""
    full = hr_index.avatar_full(employee_key)
    if os.path.isfile(full):
        return
    _download_avatar(url, employee_key)


def _remove_avatar(employee_key: str):
    path = hr_index.avatar_full(employee_key)
    if os.path.isfile(path):
        try:
            os.remove(path)
        except OSError as e:
            print(f"[HR-SYNC] remove avatar for {employee_key} failed: {e}", flush=True)


def _prune_face_embeddings(user_ids: list[int]):
    if not user_ids:
        return
    try:
        from backend.models.embedding import FaceEmbedding
        engine = create_engine(settings.DATABASE_URL.replace("+asyncpg", "+psycopg2"), pool_pre_ping=True)
        with engine.begin() as c:
            c.execute(delete(FaceEmbedding).where(FaceEmbedding.employee_id.in_(user_ids)))
    except Exception as e:
        print(f"[HR-SYNC] face-vector prune failed: {e}", flush=True)


def sync_employees() -> dict:
    """Run a full HR sync. Returns a summary dict (never raises on DB failure)."""
    hr = fetch_hr_employees()
    if hr is None:
        return {"status": "error", "detail": "HR DB unreachable", "employees": 0}

    records: list[dict] = []
    keys_now: set[str] = set()
    for item in hr:
        emp_id_str = str(item.get("employee_id") or "").strip()
        if not emp_id_str or not item.get("user_id"):
            continue
        keys_now.add(emp_id_str)
        name = (item.get("name") or "").strip() or emp_id_str
        status = item.get("status") or "active"
        url = item.get("picture_url")
        _ensure_avatar(url, emp_id_str)
        records.append({"employee_id": emp_id_str, "name": name,
                        "status": status, "user_id": int(item["user_id"]),
                        "picture_url": url})

    # Employees that vanished from HR: drop their avatar + learned vectors.
    old_keys = set(hr_index.load_index().keys())
    gone = old_keys - keys_now
    gone_uids = [rec.get("user_id") for rec in hr_index.load_index().values()
                 if rec.get("employee_id") in gone and rec.get("user_id") is not None]
    for key in gone:
        perception.notify_photo_removed(hr_index.avatar_rel(key))
        _remove_avatar(key)
    _prune_face_embeddings(gone_uids)

    hr_index.write_index(records)

    # Notify the child for every avatar so a changed image re-encodes the anchor.
    for rec in records:
        full = hr_index.avatar_full(rec["employee_id"])
        if os.path.isfile(full):
            perception.notify_photo_added(full, hr_index.avatar_rel(rec["employee_id"]))

    # Rebuild the gallery from the current index (employee-referenced only) + stored autos.
    photos = hr_index.avatar_rels()
    seed = _seed_vectors()
    perception.reload_gallery(photos, seed)
    print(f"[HR-SYNC] synced {len(records)} employee(s), removed {len(gone)}, gallery photos {len(photos)}.", flush=True)
    return {"status": "ok", "employees": len(records), "removed": len(gone), "photos": len(photos)}


def _seed_vectors() -> list[dict]:
    """Persisted auto-vectors keyed by the current anchor basename for the gallery."""
    try:
        from sqlalchemy import create_engine, select
        from backend.models.embedding import FaceEmbedding
        engine = create_engine(settings.DATABASE_URL.replace("+asyncpg", "+psycopg2"), pool_pre_ping=True)
        with engine.connect() as c:
            rows = c.execute(select(
                FaceEmbedding.vec, FaceEmbedding.source, FaceEmbedding.quality,
                FaceEmbedding.employee_id)).all()
        seed = []
        for (vec, src, q, uid) in rows:
            info = hr_index.by_user_id(int(uid)) if uid is not None else None
            if not info:
                continue
            seed.append({"key": f"{info['employee_id']}_avatar.jpg",
                         "vec": bytes(vec), "source": src, "quality": q})
        return seed
    except Exception as e:
        print(f"[HR-SYNC] seed read failed: {e}", flush=True)
        return []
