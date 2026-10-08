"""Turn alerts that an operator EXPLAINED (feedback_note) into short searchable sentences.

Why only those: counts and lists are answered exactly by SQL tools. What SQL cannot do is
"have we seen this kind of false alarm before?" - and the only rows that carry that knowledge
are the ones where an operator wrote WHY. Incremental: only rows judged since the last run.
"""
from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select

from backend.core.database import AsyncSessionLocal
from backend.models.alert import AlertLog
from backend.rag import CACHE_DIR
from backend.rag.stores.base import Chunk

TZ = ZoneInfo("Asia/Karachi")
STATE = CACHE_DIR / "alerts_state.json"
MAX_ROWS = 5000


def load_since() -> datetime | None:
    try:
        return datetime.fromisoformat(json.loads(STATE.read_text())["since"])
    except Exception:
        return None


def save_since(ts: datetime) -> None:
    STATE.write_text(json.dumps({"since": ts.isoformat()}))


def clear_state() -> None:
    STATE.unlink(missing_ok=True)


def _sentence(r: AlertLog) -> str:
    when = r.timestamp.astimezone(TZ).strftime("%Y-%m-%d %H:%M") if r.timestamp else "unknown time"
    verdict = {"down": "The operator marked this alert WRONG (false detection).",
               "up": "The operator confirmed this alert as correct."}.get(r.feedback, "Operator comment:")
    return (f"Alert #{r.id} on {when} - {r.violation_type} "
            f"(module {r.module or 'n/a'}, camera {r.camera_id}, event {r.event_type or 'n/a'}, "
            f"severity {r.severity or 'unclassified'}). {verdict} Reason: {r.feedback_note.strip()}")


async def build_alert_chunks(full: bool = False) -> tuple[list[Chunk], datetime | None]:
    stmt = (select(AlertLog)
            .where(AlertLog.feedback_note.is_not(None), AlertLog.feedback_note != "")
            .order_by(AlertLog.feedback_at.asc()).limit(MAX_ROWS))
    since = None if full else load_since()
    if since is not None:
        stmt = stmt.where(AlertLog.feedback_at > since)
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(stmt)).scalars().all()
    chunks = [Chunk(id=f"alert-{r.id}", text=_sentence(r),
                    metadata={"kind": "alert", "source": f"alert #{r.id}", "category": "alert",
                              "alert_id": r.id, "module": r.module, "camera_id": r.camera_id,
                              "feedback": r.feedback})
              for r in rows]
    newest = max((r.feedback_at for r in rows if r.feedback_at), default=None)
    return chunks, newest
