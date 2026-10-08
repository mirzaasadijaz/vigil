from datetime import date as date_type, datetime, time as time_type
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.core.database import get_db
from backend.core.dependencies import get_current_user
from backend.models.user import User
from backend.models.attendance import AttendanceRecord
from backend.services import hr_index
from zoneinfo import ZoneInfo

router = APIRouter(prefix="/attendance", tags=["attendance"])
TZ = ZoneInfo("Asia/Karachi")


def _emp_key(uid: int | None) -> str | None:
    info = hr_index.by_user_id(uid) if uid is not None else None
    return info["employee_id"] if info else None


def _emp_name(uid: int | None) -> str | None:
    info = hr_index.by_user_id(uid) if uid is not None else None
    return info.get("name") if info else None


def _photo(uid: int | None) -> str | None:
    info = hr_index.by_user_id(uid) if uid is not None else None
    if not info:
        return None
    key = info["employee_id"]
    rel = hr_index.avatar_rel(key)
    import os
    return f"/media/{rel.replace(os.sep, '/')}" if os.path.isfile(hr_index.avatar_full(key)) else None


def fmt_dt(d: date_type, t: time_type) -> str | None:
    """Combine a DATE + TIME column into a tz-aware ISO datetime for the UI."""
    if t is None:
        return None
    return datetime.combine(d, t, tzinfo=TZ).isoformat()


@router.get("/")
async def get_attendance(
    date: str = Query(default=None),
    start: str = Query(default=None),
    end: str = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    stmt = (
        select(AttendanceRecord)
        .order_by(AttendanceRecord.date.desc(), AttendanceRecord.clock_in.desc())
    )
    # Date range (start/end inclusive) takes precedence over single date.
    if start or end:
        try:
            if start:
                stmt = stmt.where(AttendanceRecord.date >= date_type.fromisoformat(start))
            if end:
                stmt = stmt.where(AttendanceRecord.date <= date_type.fromisoformat(end))
        except ValueError:
            pass
    elif date:
        try:
            filter_date = date_type.fromisoformat(date)
            stmt = stmt.where(AttendanceRecord.date == filter_date)
        except ValueError:
            pass

    result = await db.execute(stmt)
    records = result.scalars().all()

    return [
        {
            "id": r.id,
            "employee": {
                "name": _emp_name(r.employee_id) or str(r.employee_id),
                "employee_id": _emp_key(r.employee_id) or str(r.employee_id),
                "photo": _photo(r.employee_id),
            } if r.employee_id else None,
            "date": r.date.strftime("%Y-%m-%d"),
            "check_in": fmt_dt(r.date, r.clock_in),
            "check_out": fmt_dt(r.date, r.clock_out),
            "status": "Present",
        }
        for r in records
    ]
