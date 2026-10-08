"""Employees — read-only, sourced from the remote HR HTTP API.

`GET /employees/` returns the full directory (merged with recognition/attendance
stats) for the attendance/mess/overview screens; `GET /employees/hr` is a paginated
proxy of the HR API for the Employees screen; `POST /employees/sync` refreshes
avatars + the identity index + the face gallery from the API.
"""
import asyncio
import datetime
import os

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete
from backend.core.database import get_db
from backend.core.dependencies import get_current_user, require_role
from backend.models.user import User, UserRole
from backend.models.embedding import FaceEmbedding
from backend.models.attendance import AttendanceRecord
from backend.services import hr_index
from backend.services.hr_sync import fetch_hr_page, sync_employees

router = APIRouter(prefix="/employees", tags=["employees"])


@router.get("/")
async def list_employees(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Full HR directory merged with live recognition/attendance stats (for views that
    need the whole roster, e.g. attendance cards). `id`/`employee_id` is the HR code
    (string); `user_id` is the remote users.id used by the DB tables."""
    records = list(hr_index.load_index().values())

    # Learned (auto-enrolled) face-vector counts per user_id
    counts = dict((await db.execute(
        select(FaceEmbedding.employee_id, func.count())
        .where(FaceEmbedding.source == "auto")
        .group_by(FaceEmbedding.employee_id)
    )).all())
    # Today's attendance per user_id
    today = datetime.date.today()
    att_rows = (await db.execute(
        select(AttendanceRecord).where(AttendanceRecord.date == today)
    )).scalars().all()
    att_map: dict[int, dict] = {}
    for a in att_rows:
        att_map[a.employee_id] = {
            "check_in": a.clock_in.isoformat() if a.clock_in else None,
            "check_out": a.clock_out.isoformat() if a.clock_out else None,
            "status": "Late" if a.is_late else "On Time",
        }

    out = []
    for rec in records:
        key = rec["employee_id"]
        uid = rec.get("user_id")
        rel = hr_index.avatar_rel(key)
        out.append({
            "id": key,
            "user_id": uid,
            "name": rec.get("name") or key,
            "employee_id": key,
            "photo": rel,
            "picture_url": rec.get("picture_url"),
            "designation": None,
            "department": None,
            "email": None,
            "phone": None,
            "gender": None,
            "status": rec.get("status") or "active",
            "join_date": None,
            "has_anchor": os.path.isfile(hr_index.avatar_full(key)),
            "learned_samples": int(counts.get(uid, 0)) if uid else 0,
            "today_attendance": att_map.get(uid) if uid else None,
        })
    return out


@router.get("/hr")
async def hr_page(
    page: int = Query(1, ge=1),
    per_page: int = Query(10, ge=1, le=100),
    current_user: User = Depends(get_current_user),
):
    """Paginated employee roster proxied from the remote HR API (GET /api/employees)
    — powers the Employees screen. The HR API exposes id/name/employee_id/status/
    picture_url only; email/phone/designation/etc. aren't available there."""
    body = await asyncio.to_thread(fetch_hr_page, page, per_page)
    if body is None:
        raise HTTPException(status_code=502, detail="HR API unreachable")
    data = []
    for e in body.get("data") or []:
        emp_id = str(e.get("employee_id") or "").strip()
        rel = hr_index.avatar_rel(emp_id)
        has_anchor = os.path.isfile(hr_index.avatar_full(emp_id))
        data.append({
            "id": e.get("id"),
            "employee_id": emp_id,
            "user_id": e.get("id"),
            "name": e.get("name"),
            "email": None,
            "phone": None,
            "gender": None,
            "status": e.get("status") or "active",
            "date_of_joining": None,
            "biometric_emp_id": None,
            "avatar": None,
            "picture_url": f"/media/{rel.replace(os.sep, '/')}" if has_anchor else e.get("picture_url"),
        })
    meta = body.get("meta") or {}
    return {"data": data, "meta": {
        "current_page": meta.get("current_page", page),
        "per_page": meta.get("per_page", per_page),
        "total": meta.get("total", 0),
        "last_page": meta.get("last_page", 1),
    }}


@router.post("/sync")
async def sync_hr_employees(current_user: User = Depends(require_role(UserRole.admin, UserRole.supervisor))):
    """Pull the latest employee list + avatars from the remote HR API and rebuild the
    face gallery. Employees are added/edited in the Laravel HRM — the API is the source."""
    return await asyncio.to_thread(sync_employees)


@router.delete("/{employee_key}/face-vectors")
async def reset_face_vectors(
    employee_key: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin, UserRole.supervisor)),
):
    """Reset an employee's *learned* (auto-enrolled) face vectors — back to just their
    avatar anchor. The gallery will re-learn from live frames."""
    info = hr_index.load_index().get(employee_key)
    uid = info.get("user_id") if info else None
    if uid is None:
        raise HTTPException(status_code=404, detail="Employee not found")
    from backend.services import perception as face_worker
    await db.execute(delete(FaceEmbedding).where(
        FaceEmbedding.employee_id == uid, FaceEmbedding.source == "auto"))
    await db.commit()
    face_worker.notify_reset_autos(hr_index.avatar_rel(employee_key))
    return {"status": "success"}
