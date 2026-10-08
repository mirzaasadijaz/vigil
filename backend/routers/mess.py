import os
import time
import datetime
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException, Query, Body
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from backend.core.database import get_db
from backend.core.dependencies import get_current_user, require_role
from backend.models.user import User, UserRole
from backend.models.mess import MenuItem, MessSession, MessSessionItem
from backend.services import hr_index

router = APIRouter(prefix="/mess", tags=["mess"])
TZ = ZoneInfo("Asia/Karachi")

# Staged (draft) bills for open sessions: {session_id: {menu_item_id: qty}}.
# Item add/subtract only mutates this in-memory store — the DB is written once
# on close (bill confirm). Lost on backend restart, by design.
_DRAFT: dict[int, dict[int, int]] = {}

# Lightweight snapshot of open sessions so add/subtract (the hot path) does not
# touch the remote DB at all once a draft exists. Invalidated on close/delete.
_SESSION_CACHE: dict[int, tuple[float, dict]] = {}
_SESSION_CACHE_TTL = 30.0

# Cached menu (name, price) — invalidated on menu edits.
_MENU_CACHE: dict[int, tuple[str, float]] = {}
_MENU_CACHE_AT = 0.0
_MENU_CACHE_TTL = 60.0


def _uid_of(employee_key: str) -> int | None:
    info = hr_index.load_index().get(employee_key)
    return info.get("user_id") if info else None


def _fmt(dt):
    if not dt:
        return None
    local = dt.astimezone(TZ) if dt.tzinfo else dt
    return local.isoformat()


def _emp_payload(uid: int | None) -> dict | None:
    if uid is None:
        return None
    info = hr_index.by_user_id(uid) or {}
    key = info.get("employee_id") or str(uid)
    name = info.get("name") or key
    rel = hr_index.avatar_rel(key)
    return {
        "id": key,
        "name": name,
        "employee_id": key,
        "photo": f"/media/{rel.replace(os.sep, '/')}" if os.path.isfile(hr_index.avatar_full(key)) else None,
    }


def _invalidate_menu():
    global _MENU_CACHE_AT
    _MENU_CACHE.clear()
    _MENU_CACHE_AT = 0.0


async def _menu_map(db: AsyncSession) -> dict[int, tuple[str, float]]:
    """{menu_item_id: (name, price)} — cached in memory, invalidated on menu edits."""
    global _MENU_CACHE, _MENU_CACHE_AT
    if _MENU_CACHE and (time.monotonic() - _MENU_CACHE_AT) < _MENU_CACHE_TTL:
        return _MENU_CACHE
    rows = (await db.execute(select(MenuItem))).scalars().all()
    _MENU_CACHE = {m.id: (m.name, m.price) for m in rows}
    _MENU_CACHE_AT = time.monotonic()
    return _MENU_CACHE


def _snap(s: MessSession) -> dict:
    return {"id": s.id, "employee_id": s.employee_id, "camera_id": s.camera_id,
            "status": s.status, "started_at": s.started_at, "closed_at": s.closed_at,
            "total": s.total or 0.0}


def _cached_session(session_id: int) -> dict | None:
    ent = _SESSION_CACHE.get(session_id)
    if ent and (time.monotonic() - ent[0]) < _SESSION_CACHE_TTL:
        return ent[1]
    _SESSION_CACHE.pop(session_id, None)
    return None


def _cache_session(session_id: int, snap: dict):
    _SESSION_CACHE[session_id] = (time.monotonic(), snap)


def _invalidate_session(session_id: int):
    _SESSION_CACHE.pop(session_id, None)


def _draft_line(mid: int, qty: int, menu_map: dict) -> dict | None:
    """A staged line gets a synthetic negative id so the UI can add/subtract it."""
    info = menu_map.get(mid)
    if not info or qty <= 0:
        return None
    name, price = info
    return {"id": -mid, "menu_item_id": mid, "name": name, "price": price,
            "qty": qty, "amount": round(qty * price, 2)}


def _serialize_session(snap: dict, budget: dict | None = None,
                       draft: dict[int, int] | None = None,
                       committed: list | None = None,
                       menu_map: dict | None = None) -> dict:
    if draft is not None:
        items = [d for mid, qty in draft.items() if (d := _draft_line(mid, qty, menu_map or {}))]
        total = round(sum(i["amount"] for i in items), 2)
    else:
        items = [
            {"id": i.id, "menu_item_id": i.menu_item_id, "name": i.name,
             "price": i.price, "qty": i.qty, "amount": round(i.amount or 0.0, 2)}
            for i in (committed or [])
        ]
        total = round(snap["total"] or 0.0, 2)
    return {
        "id": snap["id"],
        "employee": _emp_payload(snap["employee_id"]),
        "camera_id": snap["camera_id"],
        "status": snap["status"],
        "started_at": _fmt(snap["started_at"]),
        "closed_at": _fmt(snap["closed_at"]),
        "total": total,
        "items": items,
        "budget": budget,
    }


def _serialize_receipt(s: MessSession, budget: dict) -> dict:
    return {
        "id": s.id,
        "employee": _emp_payload(s.employee_id),
        "date": _fmt(s.started_at),
        "closed_at": _fmt(s.closed_at),
        "items": [
            {"name": i.name, "price": i.price, "qty": i.qty, "amount": round(i.amount or 0.0, 2)}
            for i in (s.items or [])
        ],
        "total": round(s.total or 0.0, 2),
        "budget": budget,
    }


def _period_start(ref: datetime.date | None = None) -> datetime.date:
    """Start of the 15-day billing period containing `ref`: the 1st or the 16th of the month."""
    ref = ref or datetime.date.today()
    return ref.replace(day=1) if ref.day <= 15 else ref.replace(day=16)


def _period_bounds(start: datetime.date) -> tuple[datetime.date, datetime.date]:
    """(start, exclusive_end) of the period starting at `start`."""
    if start.day == 1:
        return start, start.replace(day=16)
    if start.month == 12:
        return start, start.replace(year=start.year + 1, month=1, day=1)
    return start, start.replace(month=start.month + 1, day=1)


def _period_label(start: datetime.date, end: datetime.date) -> str:
    last = end - datetime.timedelta(days=1)
    return f"{start.strftime('%b %d')} – {last.strftime('%b %d')}"


async def _budget(db: AsyncSession, employee_key: str | None = None,
                  period: str | None = None, uid: int | None = None) -> dict:
    """15-day mess spend for the period (1st–15th, 16th–end of month). No budget limit."""
    start = datetime.date.fromisoformat(period) if period else _period_start()
    start, end = _period_bounds(start)
    q = (
        select(MessSession.employee_id, func.coalesce(func.sum(MessSession.total), 0.0))
        .where(MessSession.status == "closed", MessSession.started_at >= start, MessSession.started_at < end)
    )
    if uid is None and employee_key:
        uid = _uid_of(employee_key)
    if uid is not None:
        q = q.where(MessSession.employee_id == uid)
    q = q.group_by(MessSession.employee_id)
    rows = dict((await db.execute(q)).all())
    base = {"period_start": start.isoformat(), "period_end": end.isoformat(),
            "label": _period_label(start, end)}
    if uid is not None:
        used = float(rows.get(uid, 0.0))
        return {**base, "used": round(used, 2)}
    return {**base, "employees": {str(k): round(float(v), 2) for k, v in rows.items()}}


# ── Menu (fixed items, admin manages) ─────────────────────────────────────────
@router.get("/menu")
async def list_menu(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    rows = (await db.execute(select(MenuItem).order_by(MenuItem.category, MenuItem.name))).scalars().all()
    return [{"id": m.id, "name": m.name, "category": m.category or "meal",
             "price": m.price, "icon": m.icon, "is_active": m.is_active} for m in rows]


@router.post("/menu")
async def add_menu_item(
    name: str = Body(..., embed=True),
    price: float = Body(..., embed=True),
    category: str = Body("meal", embed=True),
    icon: str = Body(None, embed=True),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin, UserRole.supervisor)),
):
    item = MenuItem(name=name, price=price, category=category, icon=icon, is_active=True)
    db.add(item)
    await db.commit()
    await db.refresh(item)
    _invalidate_menu()
    return {"id": item.id, "name": item.name, "price": item.price, "category": item.category}


@router.put("/menu/{item_id}")
async def update_menu_item(
    item_id: int,
    name: str = Body(None, embed=True),
    price: float = Body(None, embed=True),
    category: str = Body(None, embed=True),
    icon: str = Body(None, embed=True),
    is_active: bool = Body(None, embed=True),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin, UserRole.supervisor)),
):
    item = (await db.execute(select(MenuItem).where(MenuItem.id == item_id))).scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Menu item not found")
    if name is not None:
        item.name = name
    if price is not None:
        item.price = price
    if category is not None:
        item.category = category
    if icon is not None:
        item.icon = icon
    if is_active is not None:
        item.is_active = is_active
    await db.commit()
    _invalidate_menu()
    return {"status": "success"}


@router.delete("/menu/{item_id}")
async def delete_menu_item(
    item_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin, UserRole.supervisor)),
):
    item = (await db.execute(select(MenuItem).where(MenuItem.id == item_id))).scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Menu item not found")
    await db.delete(item)
    await db.commit()
    _invalidate_menu()
    return {"status": "success"}


# ── Sessions ──────────────────────────────────────────────────────────────────
@router.get("/sessions")
async def list_sessions(
    date: str = Query(default=None),
    start: str = Query(default=None),
    end: str = Query(default=None),
    employee_key: str | None = Query(default=None),
    status: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    stmt = (
        select(MessSession)
        .options(selectinload(MessSession.items))
        .order_by(MessSession.started_at.desc())
    )
    if status:
        stmt = stmt.where(MessSession.status == status)
    uid = _uid_of(employee_key) if employee_key else None
    if uid is not None:
        stmt = stmt.where(MessSession.employee_id == uid)
    if start or end:
        try:
            if start:
                stmt = stmt.where(MessSession.started_at >= datetime.datetime.fromisoformat(start).astimezone(TZ))
            if end:
                stmt = stmt.where(MessSession.started_at < datetime.datetime.fromisoformat(end).astimezone(TZ))
        except ValueError:
            pass
    elif date:
        try:
            d = datetime.date.fromisoformat(date)
            stmt = stmt.where(func.date(MessSession.started_at) == d)
        except ValueError:
            pass
    result = await db.execute(stmt)
    sessions = result.scalars().all()
    budget = await _budget(db)
    menu_map = await _menu_map(db)
    out = []
    for s in sessions:
        _cache_session(s.id, _snap(s))
        out.append(_serialize_session(_snap(s), None,
                                      _DRAFT.get(s.id) if s.id in _DRAFT else None,
                                      s.items, menu_map))
    return {"sessions": out, "budget": budget}


@router.get("/live-sessions")
async def live_sessions(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Open (unbilled) sessions — polled by the Mess UI to auto-open the billing modal."""
    stmt = (
        select(MessSession)
        .options(selectinload(MessSession.items))
        .where(MessSession.status == "open")
        .order_by(MessSession.started_at.desc())
    )
    result = await db.execute(stmt)
    sessions = result.scalars().all()
    budget = await _budget(db)
    menu_map = await _menu_map(db)
    out = []
    for s in sessions:
        _cache_session(s.id, _snap(s))
        out.append(_serialize_session(_snap(s), None,
                                      _DRAFT.get(s.id) if s.id in _DRAFT else None,
                                      s.items, menu_map))
    return {"sessions": out, "budget": budget}


@router.post("/sessions")
async def open_session(
    employee_key: str = Body(..., embed=True),
    camera_id: int | None = Body(None, embed=True),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin, UserRole.supervisor, UserRole.operator)),
):
    """Manually open a bill for an employee (fallback when face auto-open is off)."""
    uid = _uid_of(employee_key)
    if uid is None:
        raise HTTPException(status_code=404, detail="Employee not found")
    existing = (await db.execute(
        select(MessSession).options(selectinload(MessSession.items))
        .where(MessSession.employee_id == uid, MessSession.status == "open"))).scalar_one_or_none()
    menu_map = await _menu_map(db)
    if existing:
        _cache_session(existing.id, _snap(existing))
        return _serialize_session(_snap(existing), None,
                                   _DRAFT.get(existing.id) if existing.id in _DRAFT else None,
                                   existing.items, menu_map)
    s = MessSession(employee_id=uid, camera_id=camera_id, status="open", total=0.0)
    db.add(s)
    await db.commit()
    s = (await db.execute(select(MessSession).options(selectinload(MessSession.items))
        .where(MessSession.id == s.id))).scalar_one()
    _cache_session(s.id, _snap(s))
    return _serialize_session(_snap(s), None, None, s.items, menu_map)


@router.post("/sessions/{session_id}/items")
async def add_item(
    session_id: int,
    menu_item_id: int = Body(..., embed=True),
    qty: int = Body(1, embed=True),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin, UserRole.supervisor, UserRole.operator)),
):
    snap = _cached_session(session_id)
    if snap is None:
        s = (await db.execute(select(MessSession).where(MessSession.id == session_id))).scalar_one_or_none()
        if not s:
            raise HTTPException(status_code=404, detail="Session not found")
        snap = _snap(s)
        _cache_session(session_id, snap)
    if snap["status"] != "open":
        raise HTTPException(status_code=400, detail="Session already closed")
    menu_map = await _menu_map(db)
    if menu_item_id not in menu_map:
        raise HTTPException(status_code=404, detail="Menu item not found")
    qty = int(qty)
    if qty == 0:
        raise HTTPException(status_code=400, detail="qty must be non-zero")
    if session_id not in _DRAFT:
        rows = (await db.execute(
            select(MessSessionItem).where(MessSessionItem.session_id == session_id))).scalars().all()
        if session_id not in _DRAFT:
            _DRAFT[session_id] = {r.menu_item_id: r.qty for r in rows if r.menu_item_id}
    draft = _DRAFT[session_id]
    draft[menu_item_id] = draft.get(menu_item_id, 0) + qty
    if draft[menu_item_id] <= 0:
        draft.pop(menu_item_id, None)
    return _serialize_session(snap, None, draft, None, menu_map)


@router.delete("/sessions/{session_id}/items/{item_id}")
async def remove_item(
    session_id: int,
    item_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin, UserRole.supervisor, UserRole.operator)),
):
    snap = _cached_session(session_id)
    if snap is None:
        s = (await db.execute(select(MessSession).where(MessSession.id == session_id))).scalar_one_or_none()
        if not s:
            raise HTTPException(status_code=404, detail="Session not found")
        snap = _snap(s)
        _cache_session(session_id, snap)
    if snap["status"] != "open":
        raise HTTPException(status_code=400, detail="Session already closed")
    if session_id not in _DRAFT:
        rows = (await db.execute(
            select(MessSessionItem).where(MessSessionItem.session_id == session_id))).scalars().all()
        if session_id not in _DRAFT:
            _DRAFT[session_id] = {r.menu_item_id: r.qty for r in rows if r.menu_item_id}
    draft = _DRAFT[session_id]
    if item_id < 0:
        mid = -item_id  # staged line (synthetic negative id)
    elif item_id > 0:
        row = (await db.execute(
            select(MessSessionItem).where(MessSessionItem.session_id == session_id,
                                          MessSessionItem.id == item_id))).scalar_one_or_none()
        mid = row.menu_item_id if row else None  # legacy committed line
    else:
        mid = None
    if mid is not None:
        draft.pop(mid, None)
    return _serialize_session(snap, None, draft, None, await _menu_map(db))


@router.post("/sessions/{session_id}/close")
async def close_session(
    session_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin, UserRole.supervisor, UserRole.operator)),
):
    s = (await db.execute(
        select(MessSession).options(selectinload(MessSession.items))
        .where(MessSession.id == session_id))).scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    if s.status != "open":
        raise HTTPException(status_code=400, detail="Session already closed")
    if session_id in _DRAFT:
        draft = _DRAFT[session_id]
        menu_map = await _menu_map(db)
        for li in list(s.items):
            s.items.remove(li)
            await db.delete(li)
        for mid, qty in draft.items():
            info = menu_map.get(mid)
            if not info or qty <= 0:
                continue
            name, price = info
            s.items.append(MessSessionItem(menu_item_id=mid, name=name, price=price,
                                           qty=qty, amount=round(qty * price, 2)))
        _DRAFT.pop(session_id, None)
    s.status = "closed"
    s.closed_at = datetime.datetime.now(TZ)
    s.total = sum((i.amount or 0.0) for i in s.items)
    await db.commit()
    _invalidate_session(session_id)
    budget = await _budget(db, uid=s.employee_id)
    return {"receipt": _serialize_receipt(s, budget)}


@router.get("/sessions/{session_id}/receipt")
async def get_receipt(
    session_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    s = (await db.execute(
        select(MessSession).options(selectinload(MessSession.items))
        .where(MessSession.id == session_id))).scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    budget = await _budget(db, uid=s.employee_id)
    return _serialize_receipt(s, budget)


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    s = (await db.execute(select(MessSession).where(MessSession.id == session_id))).scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    _DRAFT.pop(session_id, None)
    _invalidate_session(session_id)
    await db.delete(s)
    await db.commit()
    return {"status": "success"}


# ── 15-day spend ────────────────────────────────────────────────────────────────
@router.get("/stats")
async def mess_stats(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    """All-time totals: total billed across every closed session + distinct employees that purchased."""
    total = (await db.execute(
        select(func.coalesce(func.sum(MessSession.total), 0.0)).where(MessSession.status == "closed"))).scalar_one()
    emp_count = (await db.execute(
        select(func.count(func.distinct(MessSession.employee_id))).where(MessSession.status == "closed"))).scalar_one()
    return {"total_spent": round(float(total), 2), "employees": int(emp_count)}


@router.get("/budget")
async def period_budget(
    period: str | None = Query(default=None, description="Period start date YYYY-MM-DD (1st or 16th)"),
    employee_key: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Per-employee 15-day mess spend. Clicking an employee on the dashboard calls
    this with their id to show their schedule for that half-month period."""
    if period:
        try:
            start = datetime.date.fromisoformat(period)
        except ValueError:
            raise HTTPException(status_code=400, detail="period must be YYYY-MM-DD (1st or 16th of a month)")
    else:
        start = _period_start()
    start, end = _period_bounds(start)

    uid = _uid_of(employee_key) if employee_key else None
    if uid is not None:
        info = hr_index.load_index().get(employee_key) or {}
        sessions = (await db.execute(
            select(MessSession).options(selectinload(MessSession.items))
            .where(MessSession.employee_id == uid, MessSession.status == "closed",
                   MessSession.started_at >= start, MessSession.started_at < end)
            .order_by(MessSession.started_at.asc()))).scalars().all()
        used = sum(s.total or 0.0 for s in sessions)
        rel = hr_index.avatar_rel(employee_key)
        return {
            "employee": {"id": employee_key, "name": info.get("name") or employee_key,
                         "employee_id": employee_key,
                         "photo": f"/media/{rel.replace(os.sep, '/')}" if os.path.isfile(hr_index.avatar_full(employee_key)) else None},
            "period_start": start.isoformat(), "period_end": end.isoformat(),
            "label": _period_label(start, end),
            "used": round(used, 2),
            "days": [{"date": _fmt(s.started_at), "total": round(s.total or 0.0, 2),
                      "items": [{"name": i.name, "qty": i.qty, "amount": round(i.amount or 0.0, 2)}
                                for i in (s.items or [])]} for s in sessions],
        }

    # Rollup for every employee that used the mess in this 15-day period
    rows = (await db.execute(
        select(MessSession.employee_id, func.coalesce(func.sum(MessSession.total), 0.0))
        .where(MessSession.status == "closed", MessSession.started_at >= start, MessSession.started_at < end)
        .group_by(MessSession.employee_id))).all()
    used_map = {int(eid): round(float(total), 2) for eid, total in rows}
    data = []
    for rec in hr_index.load_index().values():
        key = rec["employee_id"]
        uid = rec.get("user_id")
        used = used_map.get(uid, 0.0) if uid else 0.0
        rel = hr_index.avatar_rel(key)
        data.append({
            "id": key, "name": rec.get("name") or key, "employee_id": key,
            "photo": f"/media/{rel.replace(os.sep, '/')}" if os.path.isfile(hr_index.avatar_full(key)) else None,
            "used": used,
        })
    data.sort(key=lambda r: r["used"], reverse=True)
    return {"period_start": start.isoformat(), "period_end": end.isoformat(),
            "label": _period_label(start, end), "employees": data}
