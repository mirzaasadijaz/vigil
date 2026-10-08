"""
HR identity index — the lightweight source of truth for WHO an avatar belongs to.

Employees are fetched from the remote HR HTTP API (GET {HR_API_BASE}/api/employees).
The HR sync writes a small JSON index here mapping the employee_key (HR `employee_id`
string, e.g. EMP000001) to its name/status/user_id/picture_url. `user_id` is the
remote `users.id` that the app writes into face_embeddings / alert_logs /
mess_sessions / attendance_records.

The perception result-writer (a separate process) reads this file to turn an anchor
basename (`photo_base` = "<employee_id>_avatar.jpg") back into a user_id for
attendance / mess / alerts, so no DB join is needed for recognition.
"""
import json
import os
import re
import time
import uuid

from backend.core.config import settings

AVATAR_SUFFIX = "_avatar.jpg"
INDEX_PATH = os.path.join(settings.MEDIA_ROOT, "_hr_index.json")

_cache: dict | None = None
_by_uid: dict | None = None
_cache_mtime: float = 0.0


def safe_id(employee_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", employee_id) or f"emp_{uuid.uuid4().hex[:6]}"


def avatar_rel(employee_key: str) -> str:
    return os.path.join("employees", f"{safe_id(employee_key)}{AVATAR_SUFFIX}")


def avatar_full(employee_key: str) -> str:
    return os.path.join(settings.MEDIA_ROOT, avatar_rel(employee_key))


def key_of(photo_base: str) -> str | None:
    """employee_key derived from an anchor basename ('EMP000001_avatar.jpg' -> 'EMP000001')."""
    if not photo_base:
        return None
    base = os.path.basename(photo_base.replace("\\", "/"))
    if base.endswith(AVATAR_SUFFIX):
        return base[: -len(AVATAR_SUFFIX)]
    return None


def write_index(records: list[dict]) -> None:
    """Persist the HR directory: list of {employee_id, name, status, user_id, picture_url}."""
    os.makedirs(settings.MEDIA_ROOT, exist_ok=True)
    with open(INDEX_PATH, "w") as f:
        json.dump(records, f)
    global _cache, _by_uid, _cache_mtime
    _cache = {r["employee_id"]: r for r in records}
    _by_uid = {r.get("user_id"): r for r in records if r.get("user_id") is not None}
    _cache_mtime = 0.0  # force refresh readers
    print(f"[HR-INDEX] wrote {len(records)} employee(s) -> {INDEX_PATH}", flush=True)


def _raw_records() -> list[dict]:
    try:
        with open(INDEX_PATH) as f:
            return json.load(f)
    except Exception:
        return []


def load_index() -> dict:
    """employee_key -> {employee_id, name, status, user_id, picture_url}. Reads the JSON
    fresh when it changes on disk, so long-lived processes (the writer) see HR updates."""
    global _cache, _by_uid, _cache_mtime
    try:
        mtime = os.path.getmtime(INDEX_PATH)
    except OSError:
        return dict(_cache or {})
    if _cache is None or mtime != _cache_mtime:
        records = _raw_records()
        if records:
            _cache = {r["employee_id"]: r for r in records}
            _by_uid = {r.get("user_id"): r for r in records if r.get("user_id") is not None}
            _cache_mtime = mtime
    return dict(_cache or {})


def info_of(photo_base: str) -> dict | None:
    """Full HR record for an anchor basename, or None if it isn't a known HR avatar."""
    key = key_of(photo_base)
    if not key:
        return None
    return load_index().get(key)


def name_of(photo_base: str) -> str | None:
    info = info_of(photo_base)
    return info["name"] if info else None


def user_id_of(photo_base: str) -> int | None:
    """Remote users.id for an anchor basename, or None."""
    info = info_of(photo_base)
    return info.get("user_id") if info else None


def by_user_id(user_id: int) -> dict | None:
    """HR record for a remote users.id (reverse lookup used by routers)."""
    if user_id is None:
        return None
    load_index()
    return (_by_uid or {}).get(int(user_id))


def avatar_rels() -> list[str]:
    """Rel paths of every avatar currently in the index (the full recognition gallery)."""
    return [avatar_rel(k) for k in load_index().keys()]
