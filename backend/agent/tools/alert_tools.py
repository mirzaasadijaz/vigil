"""SQL tools over alert_logs: search_alerts, count_alerts. Read-only and bounded."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from backend.agent.vision import media_link
from backend.core.database import AsyncSessionLocal
from backend.models.alert import AlertLog

TZ = ZoneInfo("Asia/Karachi")
MAX_ROWS = 50

_FILTER_PROPS = {
    "module": {"type": "string", "description": "AI module, e.g. attendance, mess"},
    "camera_id": {"type": "integer"},
    "employee_id": {"type": "integer", "description": "users.id of the recognised person"},
    "event_type": {"type": "string", "description": "entry, exit, idle, intrusion, recognized, unknown"},
    "severity": {"type": "string", "description": "e.g. critical, high, medium, low"},
    "feedback": {"type": "string", "enum": ["up", "down", "none"],
                 "description": "operator verdict; 'none' = not yet judged"},
    "date_from": {"type": "string", "description": "YYYY-MM-DD, inclusive (Asia/Karachi)"},
    "date_to": {"type": "string", "description": "YYYY-MM-DD, inclusive (Asia/Karachi)"},
}

SCHEMAS = [
    {"name": "search_alerts",
     "description": "List individual alerts, newest first. Use for 'show me', 'latest', 'what happened'. "
                    "Each alert carries its own snapshot link (null when it has none).",
     "input_schema": {"type": "object", "properties": {
         **_FILTER_PROPS,
         "limit": {"type": "integer", "description": f"max {MAX_ROWS}, default 10"}}}},
    {"name": "count_alerts",
     "description": "Count alerts, optionally grouped. Use for 'how many', 'which camera has most', trends.",
     "input_schema": {"type": "object", "properties": {
         **_FILTER_PROPS,
         "group_by": {"type": "string",
                      "enum": ["module", "severity", "event_type", "camera_id",
                               "violation_type", "feedback", "day", "hour"],
                      "description": "omit for a single total"}}}},
]


def _alerts_router():
    # Lazy: importing the router pulls in the whole perception stack. Only needed at call time,
    # and reusing its filters keeps chatbot numbers identical to the dashboard's.
    from backend.routers import alerts
    return alerts


def _filters(a: dict):
    return (a.get("module"), a.get("camera_id"), a.get("employee_id"), a.get("event_type"),
            a.get("severity"), a.get("feedback"), a.get("date_from"), a.get("date_to"))


async def search_alerts(a: dict) -> dict:
    ar = _alerts_router()
    limit = max(1, min(int(a.get("limit") or 10), MAX_ROWS))
    stmt = ar._filtered(select(AlertLog).order_by(AlertLog.timestamp.desc()), *_filters(a))
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(stmt.limit(limit))).scalars().all()
    out = []
    for r in rows:
        ts = r.timestamp.astimezone(TZ) if r.timestamp is not None else None
        out.append({
            "id": r.id,
            "time": ts.strftime("%Y-%m-%d %H:%M:%S") if ts else None,
            "violation_type": r.violation_type, "module": r.module, "camera_id": r.camera_id,
            "event_type": r.event_type, "severity": r.severity,
            "person": (ar._emp_name(r.employee_id) or str(r.employee_id)) if r.employee_id
                      else ("unknown" if r.unknown_person else None),
            "likely_candidate": r.likely_candidate,
            "feedback": r.feedback, "feedback_note": r.feedback_note,
            "snapshot": media_link(r.snapshot) if r.snapshot else None,
        })
    return {"count": len(out), "alerts": out}


async def count_alerts(a: dict) -> dict:
    ar = _alerts_router()
    group_by = a.get("group_by")
    local_ts = func.timezone("Asia/Karachi", AlertLog.timestamp)
    cols = {
        "module": AlertLog.module, "severity": AlertLog.severity,
        "event_type": AlertLog.event_type, "camera_id": AlertLog.camera_id,
        "violation_type": AlertLog.violation_type, "feedback": AlertLog.feedback,
        "day": func.date_trunc("day", local_ts), "hour": func.date_trunc("hour", local_ts),
    }
    async with AsyncSessionLocal() as db:
        if not group_by:
            stmt = ar._filtered(select(func.count(AlertLog.id)), *_filters(a))
            return {"total": (await db.execute(stmt)).scalar_one()}
        if group_by not in cols:
            return {"error": f"group_by must be one of {list(cols)}"}
        col = cols[group_by]
        stmt = ar._filtered(select(col.label("k"), func.count(AlertLog.id).label("n")), *_filters(a))
        stmt = stmt.group_by(col).order_by(func.count(AlertLog.id).desc()).limit(100)
        rows = (await db.execute(stmt)).all()
    return {"group_by": group_by,
            "groups": [{"key": (k.isoformat() if isinstance(k, datetime) else k), "count": n}
                       for k, n in rows]}


HANDLERS = {"search_alerts": search_alerts, "count_alerts": count_alerts}
