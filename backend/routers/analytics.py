"""Module-driven analytics rollups over the unified event store (AlertLog)."""
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from zoneinfo import ZoneInfo
from backend.core.database import get_db
from backend.core.dependencies import get_current_user
from backend.models.user import User
from backend.models.alert import AlertLog

router = APIRouter(prefix="/analytics", tags=["analytics"])
TZ = ZoneInfo("Asia/Karachi")


@router.get("/summary")
async def summary(
    hours: int = 24,
    module: str | None = None,
    camera_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Aggregate events by module, by camera, and by hour for the last N hours."""
    since = datetime.now(TZ) - timedelta(hours=hours)

    def _filtered(stmt):
        stmt = stmt.where(AlertLog.timestamp >= since)
        if module:
            stmt = stmt.where(AlertLog.module == module)
        if camera_id is not None:
            stmt = stmt.where(AlertLog.camera_id == camera_id)
        return stmt

    by_module = (await db.execute(_filtered(
        select(AlertLog.module, func.count()).group_by(AlertLog.module)
    ))).all()
    by_camera = (await db.execute(_filtered(
        select(AlertLog.camera_id, func.count()).group_by(AlertLog.camera_id)
    ))).all()
    by_type = (await db.execute(_filtered(
        select(AlertLog.event_type, func.count()).group_by(AlertLog.event_type)
    ))).all()
    total = (await db.execute(_filtered(select(func.count()).select_from(AlertLog)))).scalar() or 0

    return {
        "hours": hours,
        "total": total,
        "by_module": [{"module": m or "unknown", "count": c} for m, c in by_module],
        "by_camera": [{"camera_id": cid, "count": c} for cid, c in by_camera],
        "by_event_type": [{"event_type": t or "unknown", "count": c} for t, c in by_type],
    }
