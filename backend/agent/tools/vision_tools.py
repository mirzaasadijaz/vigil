"""Vision tools: let the agent LOOK at alert snapshots.

search_snapshots (CLIP) only ranks images by similarity - it cannot count, read or confirm.
These two tools hand the actual pixels to a vision model:

  inspect_alert_image  ONE alert, strong model.
                       "how many chairs were in the recent alert?"
  scan_alerts_for      MANY alerts: metadata (camera/date/module) picks the alerts in SQL,
                       a light model screens each snapshot, a strong model confirms hits.
                       "what time did the red dust bin come on camera OT-Ch 8?"

Read-only. Never returns file-system paths, only /media/... links like search_snapshots does.
"""
from __future__ import annotations

import asyncio
import re
import time
from collections import Counter
from datetime import timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from backend.agent import vision
from backend.agent.tools import alert_tools
from backend.core.config import settings
from backend.core.database import AsyncSessionLocal
from backend.models.alert import AlertLog
from backend.models.camera import CameraDevice

TZ = ZoneInfo("Asia/Karachi")
HARD_MAX_SCAN = 200            # no matter what the model asks for

SCHEMAS = [
    {"name": "inspect_alert_image",
     "description": "LOOK at the snapshot of ONE alert with a vision model and answer a question "
                    "about what is in it (count chairs, colour of a bag, what someone is holding, "
                    "is a door open). Leave alert_id empty for 'recent/latest alert' (optionally "
                    "with camera). Question must be short English. Use this instead of telling "
                    "the user to check the dashboard.",
     "input_schema": {"type": "object", "required": ["question"], "properties": {
         "question": {"type": "string", "description": "English, e.g. 'How many chairs are visible?'"},
         "alert_id": {"type": "integer"},
         "camera": {"type": "string",
                    "description": "camera name (e.g. 'OT-Ch 8') or numeric id; only used when "
                                   "alert_id is empty"}}}},
    {"name": "scan_alerts_for",
     "description": "SCAN the snapshots of many alerts for something visual and report which "
                    "alerts contain it and when. Metadata (camera, dates, module) picks the "
                    "alerts first, then a fast model screens each snapshot and a stronger model "
                    "confirms. Use for 'what time did the red dust bin appear on camera X', "
                    "'which alerts show a person wearing a helmet'. Pass the narrowest date range "
                    "the user implies (none given -> today). For 'what time did X come/first "
                    "appear' use order=oldest_first + stop_at_first_hit=true; for 'when was X "
                    "last seen' use order=newest_first + stop_at_first_hit=true.",
     "input_schema": {"type": "object", "required": ["target"], "properties": {
         "target": {"type": "string", "description": "short English noun phrase, e.g. 'red dust bin'"},
         "camera": {"type": "string", "description": "camera name (e.g. 'OT-Ch 8') or numeric id"},
         "module": {"type": "string", "description": "AI module, e.g. attendance, mess"},
         "date_from": {"type": "string", "description": "YYYY-MM-DD, inclusive (Asia/Karachi)"},
         "date_to": {"type": "string", "description": "YYYY-MM-DD, inclusive (Asia/Karachi)"},
         "order": {"type": "string", "enum": ["newest_first", "oldest_first"]},
         "stop_at_first_hit": {"type": "boolean",
                               "description": "stop at the first confirmed match in that order"},
         "detail_question": {"type": "string",
                             "description": "optional extra question answered for each match, "
                                            "e.g. 'how many people are next to it?'"},
         "max_scan": {"type": "integer",
                      "description": f"snapshots to examine, default {getattr(settings, 'VISION_MAX_SCAN', 60)}, "
                                     f"max {HARD_MAX_SCAN}"}}}},
]

_HAS_SNAPSHOT = (AlertLog.snapshot.is_not(None), AlertLog.snapshot != "")


# ── small helpers ────────────────────────────────────────────────────────────
def _clean(s, n: int = 200) -> str:
    """Model-supplied text goes into a prompt: one line, bounded."""
    return re.sub(r"\s+", " ", str(s or "")).strip()[:n]


def _yes(v) -> bool:
    return v is True or str(v).strip().lower() == "true"


def _norm(s) -> str:
    return re.sub(r"[\s_\-]+", "", str(s).lower())


def _row(r) -> dict:
    ts = r.timestamp
    if ts is not None and ts.tzinfo is None:        # timestamps are stored as UTC
        ts = ts.replace(tzinfo=timezone.utc)
    ts = ts.astimezone(TZ) if ts is not None else None
    return {"id": r.id, "snapshot": r.snapshot, "camera_id": r.camera_id,
            "time": ts.strftime("%Y-%m-%d %H:%M:%S") if ts else None}


async def _resolve_camera(ref) -> tuple[int | None, str | None]:
    """'OT-Ch 8' / 'ot ch8' / '8' -> (camera_id, None) or (None, error for the agent).

    Exact name wins over substring, so 'OT-Ch 8' never matches 'OT-Ch 80'."""
    ref = str(ref).strip()
    async with AsyncSessionLocal() as db:
        cams = (await db.execute(select(CameraDevice.id, CameraDevice.name))).all()
    if ref.isdigit() and any(c.id == int(ref) for c in cams):
        return int(ref), None
    key = _norm(ref)
    pool = [c for c in cams if _norm(c.name) == key] or \
           [c for c in cams if key and key in _norm(c.name)]
    if len(pool) == 1:
        return pool[0].id, None
    if not pool:
        return None, f"no camera matches '{ref}' - call list_cameras"
    return None, (f"'{ref}' matches several cameras: "
                  + ", ".join(f"{c.id}={c.name}" for c in pool[:8]) + ". Ask which one.")


async def _candidates(camera_id, module, date_from, date_to, newest_first: bool, limit: int):
    """(rows, total): alerts that HAVE a snapshot and match the metadata filters.

    Uses the alerts router's own _filtered(), so 'camera 8 today' means exactly what the
    dashboard means by it. Bounded by `limit`; `total` says how many more exist."""
    ar = alert_tools._alerts_router()

    def flt(stmt):
        return ar._filtered(stmt.where(*_HAS_SNAPSHOT), module, camera_id, None, None, None,
                            None, date_from, date_to)

    order = ((AlertLog.timestamp.desc(), AlertLog.id.desc()) if newest_first
             else (AlertLog.timestamp.asc(), AlertLog.id.asc()))
    cols = select(AlertLog.id, AlertLog.snapshot, AlertLog.timestamp, AlertLog.camera_id)
    async with AsyncSessionLocal() as db:
        total = (await db.execute(flt(select(func.count()).select_from(AlertLog)))).scalar_one()
        rows = (await db.execute(flt(cols).order_by(*order).limit(limit))).all()
    return [_row(r) for r in rows], int(total)


# ── tool 1: one alert ────────────────────────────────────────────────────────
async def inspect_alert_image(a: dict) -> dict:
    question = _clean(a.get("question"), 300)
    if not question:
        return {"error": "question is required"}

    if a.get("alert_id") is not None:
        async with AsyncSessionLocal() as db:
            r = (await db.execute(select(AlertLog.id, AlertLog.snapshot, AlertLog.timestamp,
                                         AlertLog.camera_id)
                                  .where(AlertLog.id == int(a["alert_id"])))).first()
        if r is None:
            return {"error": f"alert {a['alert_id']} not found"}
        if not r.snapshot:
            return {"error": f"alert {r.id} has no snapshot"}
        row = _row(r)
    else:
        cam_id = None
        if a.get("camera") not in (None, ""):
            cam_id, err = await _resolve_camera(a["camera"])
            if err:
                return {"error": err}
        # newest few: skip any whose file was already cleaned up by retention
        rows, _ = await _candidates(cam_id, None, None, None, True, 5)
        row = next((r for r in rows if vision.snapshot_path(r["snapshot"])), None)
        if row is None:
            return {"error": "no recent alert with a snapshot file was found"}

    try:
        answer = await vision.ask("strong", row["snapshot"], vision.INSPECT.format(question=question))
    except vision.SnapshotMissing:
        return {"error": f"the snapshot file of alert {row['id']} no longer exists",
                "alert_id": row["id"], "time": row["time"]}
    except vision.VisionError as e:
        return {"error": str(e), "alert_id": row["id"]}
    return {"alert_id": row["id"], "time": row["time"], "camera_id": row["camera_id"],
            "answer": answer, "snapshot": vision.media_link(row["snapshot"]),
            "sources": [f"alert #{row['id']}"]}


# ── tool 2: many alerts (cascade) ────────────────────────────────────────────
async def _check(r: dict, target: str, detail: str | None, stats: Counter) -> dict | None:
    """Cascade for ONE alert. Returns a hit dict, or None (and records why in `stats`)."""
    snap = r["snapshot"]
    try:
        # Stage 1 - light model. Only a confident "no" ends the story here; "yes", "maybe"
        # and unparseable replies all escalate, so the cheap model can't silently lose a hit.
        screen = vision.parse_json(await vision.ask("light", snap, vision.SCREEN.format(target=target)))
        if (screen is not None and not _yes(screen.get("present"))
                and str(screen.get("confidence", "")).strip().lower() == "high"):
            stats["rejected_by_light"] += 1
            return None

        # Stage 2 - strong model confirms (and answers the extra question, if any).
        stats["checked_by_strong"] += 1
        extra = f"Also answer this question about the frame: {detail}\n" if detail else ""
        verdict = vision.parse_json(
            await vision.ask("strong", snap, vision.CONFIRM.format(target=target, extra=extra)))
    except vision.SnapshotMissing:
        stats["file_missing"] += 1
        return None
    except vision.VisionError:
        stats["failed"] += 1
        return None

    if verdict is None:
        stats["failed"] += 1
        return None
    if not _yes(verdict.get("present")):
        stats["rejected_by_strong"] += 1
        return None
    hit = {"alert_id": r["id"], "time": r["time"], "camera_id": r["camera_id"],
           "where": _clean(verdict.get("where"), 120) or None,
           "snapshot": vision.media_link(snap)}
    if detail:
        hit["answer"] = _clean(verdict.get("answer"), 300) or None
    return hit


async def scan_alerts_for(a: dict) -> dict:
    target = _clean(a.get("target"), 120)
    if not target:
        return {"error": "target is required, e.g. 'red dust bin'"}
    detail = _clean(a.get("detail_question"), 200) or None

    cam_id = None
    if a.get("camera") not in (None, ""):
        cam_id, err = await _resolve_camera(a["camera"])
        if err:
            return {"error": err}

    cap = max(1, min(int(a.get("max_scan") or getattr(settings, "VISION_MAX_SCAN", 15)), HARD_MAX_SCAN))
    newest = a.get("order") != "oldest_first"
    first_only = bool(a.get("stop_at_first_hit"))
    rows, total = await _candidates(cam_id, a.get("module"), a.get("date_from"),
                                    a.get("date_to"), newest, cap)
    if not rows:
        return {"target": target, "candidates": 0, "scanned": 0, "hits": [], "sources": [],
                "note": "No alerts with a snapshot match the camera/date filters."}

    # Chunks of VISION_CONCURRENCY run in parallel, in order, so "first hit" stays correct
    # and we can stop early without having paid for the rest.
    width = max(1, int(getattr(settings, "VISION_CONCURRENCY", 5)))
    budget = float(getattr(settings, "VISION_SCAN_BUDGET_S", 60))
    stats: Counter = Counter()
    hits: list[dict] = []
    scanned, timed_out, t0 = 0, False, time.monotonic()
    for i in range(0, len(rows), width):
        if time.monotonic() - t0 > budget:
            timed_out = True
            break
        batch = rows[i:i + width]
        results = await asyncio.gather(*(_check(r, target, detail, stats) for r in batch))
        scanned += len(batch)
        hits += [h for h in results if h]
        if first_only and hits:
            hits = hits[:1]
            break

    stopped_early = first_only and bool(hits)
    seen = rows[:scanned]
    out = {
        "target": target,
        "candidates": total,                       # all alerts with snapshots matching the filters
        "scanned": scanned,
        "scan_order": "newest_first" if newest else "oldest_first",
        "span_scanned": [seen[0]["time"], seen[-1]["time"]] if seen else None,
        "stopped_at_first_hit": stopped_early,
        "hits": hits,
        "stats": dict(stats),
        "sources": [f"alert #{h['alert_id']}" for h in hits],
    }
    if not stopped_early and scanned < total:
        why = "time budget reached" if timed_out else f"max_scan={cap} reached"
        out["note"] = (f"Scanned only {scanned} of {total} matching alerts ({why}). "
                       "Not finding it is NOT proof it never appeared - narrow date_from/date_to "
                       "to cover the rest.")
    if stats.get("failed") or stats.get("file_missing"):
        out["warning"] = (f"{stats.get('failed', 0)} snapshots could not be analysed and "
                          f"{stats.get('file_missing', 0)} files were missing; they are not "
                          "counted as 'absent'.")
    return out


HANDLERS = {"inspect_alert_image": inspect_alert_image, "scan_alerts_for": scan_alerts_for}
