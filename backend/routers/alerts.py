import os
from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload
from backend.core.database import get_db
from backend.core.dependencies import get_current_user, require_role
from backend.models.user import User, UserRole
from backend.models.alert import AlertLog
from backend.core.config import settings
from backend.services import hr_index
from backend import services
from datetime import datetime
from zoneinfo import ZoneInfo

router = APIRouter(prefix="/alerts", tags=["alerts"])
TZ = ZoneInfo("Asia/Karachi")


def _emp_key(uid: int | None) -> str | None:
    info = hr_index.by_user_id(uid) if uid is not None else None
    return info["employee_id"] if info else None


def _emp_name(uid: int | None) -> str | None:
    info = hr_index.by_user_id(uid) if uid is not None else None
    return info.get("name") if info else None


def _build_name(log: AlertLog) -> str:
    if log.employee_id:
        name = _emp_name(log.employee_id) or str(log.employee_id)
        if log.distance_score is not None:
            name += f" (Dist: {log.distance_score:.4f})"
        return name
    if log.unknown_person:
        if log.likely_candidate:
            name = f"Unknown | Likely: {log.likely_candidate}"
            if log.distance_score is not None:
                name += f" (Dist: {log.distance_score:.4f})"
            return name
        return "Unknown"
    return "Unidentified"


def _day_bounds(date_from: str | None, date_to: str | None):
    """Turn two calendar dates into a UTC half-open range.

    The dates come from a date picker and mean LOCAL days - the operator asking for
    "1 September" means the 1st in Asia/Karachi, which is what every timestamp on this
    dashboard is rendered in. Comparing them against UTC directly would silently shift
    the window by five hours and drop the evening's alerts off the end of the day.

    `date_to` is INCLUSIVE: picking the same date twice must return that whole day, not
    an empty range, because that is what "from the 1st to the 1st" means to a person.
    """
    from datetime import datetime, timedelta
    lo = hi = None
    if date_from:
        lo = datetime.strptime(date_from, "%Y-%m-%d").replace(tzinfo=TZ)
    if date_to:
        hi = (datetime.strptime(date_to, "%Y-%m-%d").replace(tzinfo=TZ)
              + timedelta(days=1))          # inclusive of the whole end day
    return lo, hi


def _filtered(stmt, module, camera_id, employee_id, event_type, severity,
              feedback, date_from, date_to):
    """Every filter in one place, so the list and the count can never disagree.

    If they drift, the pager shows a page count for one query while listing rows from
    another - which looks like missing alerts and is very hard to see."""
    if module:
        stmt = stmt.where(AlertLog.module == module)
    if camera_id is not None:
        stmt = stmt.where(AlertLog.camera_id == camera_id)
    if employee_id is not None:
        stmt = stmt.where(AlertLog.employee_id == employee_id)
    if event_type:
        stmt = stmt.where(AlertLog.event_type == event_type)
    if severity:
        stmt = stmt.where(AlertLog.severity == severity)
    if feedback:
        stmt = (stmt.where(AlertLog.feedback.is_(None)) if feedback == "none"
                else stmt.where(AlertLog.feedback == feedback))
    lo, hi = _day_bounds(date_from, date_to)
    if lo is not None:
        stmt = stmt.where(AlertLog.timestamp >= lo)
    if hi is not None:
        stmt = stmt.where(AlertLog.timestamp < hi)
    return stmt


@router.get("/count")
async def count_alerts(
    module: str | None = None,
    camera_id: int | None = None,
    employee_id: int | None = None,
    event_type: str | None = None,
    severity: str | None = None,
    feedback: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """How many alerts match, for the pager.

    Separate from the list rather than folded into it, because six existing callers
    expect `/alerts/` to return a bare ARRAY. Changing that shape to {items, total}
    would break the toaster, the sidebar, the feed, attendance and the employee detail
    panel, all to save one request."""
    stmt = _filtered(select(func.count()).select_from(AlertLog), module, camera_id,
                     employee_id, event_type, severity, feedback, date_from, date_to)
    return {"total": int((await db.execute(stmt)).scalar() or 0)}


@router.get("/")
async def get_alerts(
    limit: int = 100,
    offset: int = 0,
    module: str | None = None,
    camera_id: int | None = None,
    employee_id: int | None = None,
    event_type: str | None = None,
    severity: str | None = None,
    feedback: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Alerts, newest first.

    Returns a bare array - see count_alerts for why that shape is preserved. Every new
    parameter is optional, so all existing callers keep working unchanged."""
    stmt = (
        select(AlertLog)
        .order_by(AlertLog.timestamp.desc())
    )
    stmt = _filtered(stmt, module, camera_id, employee_id, event_type, severity,
                     feedback, date_from, date_to)
    # Bound the page size. Without this a typo in the URL can ask for the whole table
    # and the response is built row by row in Python.
    limit = max(1, min(int(limit), 1000))
    result = await db.execute(stmt.offset(max(0, int(offset))).limit(limit))
    logs = result.scalars().all()
    data = []
    for l in logs:
        ts = l.timestamp.astimezone(TZ) if l.timestamp.tzinfo else l.timestamp
        key = _emp_key(l.employee_id)
        data.append({
            "id": l.id,
            "violation_type": l.violation_type,
            "timestamp": ts.isoformat(),
            "unknown_person": l.unknown_person,
            "likely_candidate": l.likely_candidate,
            "distance_score": l.distance_score,
            "snapshot": f"/media/{l.snapshot}" if l.snapshot else None,
            "employee": {"name": _emp_name(l.employee_id)} if l.employee_id else None,
            "employee_id": key,
            "module": l.module,
            "camera_id": l.camera_id,
            "event_type": l.event_type,
            # Written by the false-detection layer. NULL for rows created before
            # it existed or while it is disabled - the UI treats NULL as "not
            # classified" and falls back to its own mapping, never as a level.
            "severity": l.severity,
            # NULL means NOT JUDGED, which the UI must render differently from
            # "judged correct" - otherwise every untouched alert looks approved.
            "feedback": l.feedback,
            "feedback_note": l.feedback_note,
            "value": l.value,
            # Legacy fields for backward compat
            "name": _build_name(l),
            "violation": l.violation_type,
            "snapshot_url": f"/media/{l.snapshot}" if l.snapshot else None,
        })
    return data


@router.delete("/")
async def clear_alerts(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin, UserRole.supervisor)),
):
    result = await db.execute(select(AlertLog))
    logs = result.scalars().all()
    for log in logs:
        if log.snapshot:
            try:
                path = os.path.join(settings.MEDIA_ROOT, log.snapshot)
                if os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass
        await db.delete(log)
    await db.commit()

    mgr = services.camera_manager
    with mgr.lock:
        for inst in mgr.instances.values():
            inst.last_log_time = {}
    return {"status": "success"}


@router.post("/{alert_id}/feedback")
async def set_alert_feedback(
    alert_id: int,
    payload: dict = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Record whether an alert was right or wrong.

    `{"feedback": "up" | "down" | null, "note": "optional free text"}`

    Passing null CLEARS the judgement rather than recording a neutral one. "Nobody has
    looked at this" and "somebody looked and it was fine" are different facts and the
    accuracy figures depend on not conflating them - so there is no third value.

    Any authenticated user may judge an alert. The people watching the screens are the
    ones who know whether the bag was really abandoned, and putting this behind an admin
    role would collect almost no data, which defeats the point."""
    raw = payload.get("feedback")
    verdict = None if raw in (None, "", "null") else str(raw).strip().lower()
    if verdict not in (None, "up", "down"):
        raise HTTPException(status_code=400,
                            detail="feedback must be 'up', 'down' or null")
    note = payload.get("note")
    note = str(note).strip()[:2000] if note else None

    alert = (await db.execute(
        select(AlertLog).where(AlertLog.id == alert_id))).scalar_one_or_none()
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")

    alert.feedback = verdict
    alert.feedback_by = current_user.id if verdict else None
    alert.feedback_at = datetime.now(TZ) if verdict else None
    # Keep a note only alongside a verdict; an orphan note explains nothing.
    alert.feedback_note = note if verdict else None
    await db.commit()
    return {"id": alert.id, "feedback": alert.feedback,
            "feedback_note": alert.feedback_note}


@router.get("/feedback/summary")
async def feedback_summary(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Per-module accuracy, as judged by the people watching.

    `judged` is the denominator on purpose. Dividing by TOTAL alerts would report a
    module nobody has reviewed as 100% accurate."""
    rows = (await db.execute(
        select(AlertLog.module, AlertLog.event_type, AlertLog.feedback,
               func.count().label("n"))
        .where(AlertLog.feedback.isnot(None))
        .group_by(AlertLog.module, AlertLog.event_type, AlertLog.feedback)
    )).all()
    agg: dict = {}
    for module, event_type, fb, n in rows:
        key = (module or "unknown", event_type or "-")
        e = agg.setdefault(key, {"module": key[0], "event_type": key[1],
                                 "up": 0, "down": 0})
        e["up" if fb == "up" else "down"] += int(n)
    out = []
    for e in agg.values():
        judged = e["up"] + e["down"]
        e["judged"] = judged
        e["accuracy"] = round(e["up"] / judged, 3) if judged else None
        out.append(e)
    out.sort(key=lambda e: (-e["down"], -e["judged"]))
    return {"by_event": out,
            "totals": {"up": sum(e["up"] for e in out),
                       "down": sum(e["down"] for e in out),
                       "judged": sum(e["judged"] for e in out)}}
