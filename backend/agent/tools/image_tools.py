"""search_snapshots: find alerts by visual description (clothes, colours, objects)."""
from datetime import datetime
from zoneinfo import ZoneInfo

from backend.rag import image_index

TZ = ZoneInfo("Asia/Karachi")

SCHEMAS = [{
    "name": "search_snapshots",
    "description": "Find alerts by what is visible in the camera snapshot (clothing, colours, "
                   "objects). Query MUST be a short English description, e.g. 'person wearing "
                   "a black dress'. Returns alert ids with time and similarity, plus how many "
                   "snapshots were searched. Use for 'kab aya' questions about appearance.",
    "input_schema": {"type": "object", "required": ["query"], "properties": {
        "query": {"type": "string", "description": "English visual description"},
        "unknown_only": {"type": "boolean", "description": "only unknown-person alerts"},
        "camera_id": {"type": "integer"},
        "date_from": {"type": "string", "description": "YYYY-MM-DD (Asia/Karachi)"},
        "date_to": {"type": "string", "description": "YYYY-MM-DD (Asia/Karachi)"},
        "k": {"type": "integer", "description": "1-10, default 5"}}},
}]


async def search_snapshots(a: dict) -> dict:
    await image_index.index_new(100, throttle=30)     # cheap catch-up; bulk backfill = CLI
    k = max(1, min(int(a.get("k") or 5), 10))
    hits = await image_index.search(a["query"], k, bool(a.get("unknown_only")),
                                    a.get("camera_id"), a.get("date_from"), a.get("date_to"))
    searched = image_index.count()
    if not hits:
        return {"found": 0, "searched": searched, "sources": [],
                "note": "No indexed snapshots match" if searched else "Snapshot index is empty"}
    out = [{"alert_id": m["alert_id"],
            "time": datetime.fromtimestamp(m["ts"], TZ).strftime("%Y-%m-%d %H:%M"),
            "camera_id": m["camera_id"] or None, "unknown": m["unknown"],
            "similarity": round(s, 3),
            "snapshot": "/media/" + image_index._rel(m["snapshot"])} for m, s in hits]
    return {"found": len(out), "searched": searched, "matches": out,
            "sources": [f"alert #{o['alert_id']}" for o in out]}


HANDLERS = {"search_snapshots": search_snapshots}