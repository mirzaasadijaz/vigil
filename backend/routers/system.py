"""System maintenance endpoints — apply dashboard changes to the LIVE pipeline."""
import os

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.database import get_db
from backend.core.dependencies import require_role
from backend.models.user import User, UserRole
from backend.models.embedding import FaceEmbedding
from backend.models.camera import CameraDevice
from backend.services import perception
from backend.services import hr_index
from backend.services.camera_manager import camera_manager

router = APIRouter(prefix="/system", tags=["system"])


@router.post("/reload")
async def reload_pipeline(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin, UserRole.supervisor)),
):
    """Enforce dashboard changes on the running perception pipeline WITHOUT a restart:
      1. Rebuild the face gallery from the HR avatar set on disk (drops stale/renamed/
         orphan anchors) and re-seed the persisted auto-vectors.
      2. Refresh each running camera's assigned AI modules from the DB, so camera /
         module edits take effect without needing a fresh live-view connection.
    """
    # 1. Face gallery — rebuild anchors from the HR index avatar list + re-seed auto-vectors.
    photos = hr_index.avatar_rels()
    rows = (await db.execute(
        select(FaceEmbedding.vec, FaceEmbedding.source, FaceEmbedding.quality, FaceEmbedding.employee_id)
        .where(FaceEmbedding.source == "auto")
    )).all()
    seed = []
    for (vec, src, q, uid) in rows:
        info = hr_index.by_user_id(int(uid)) if uid is not None else None
        if info:
            seed.append({"key": f"{info['employee_id']}_avatar.jpg",
                         "vec": bytes(vec), "source": src, "quality": q})
    queued = perception.reload_gallery(photos, seed)

    # 2. Refresh running camera instances' module assignments from the DB.
    cams = (await db.execute(select(CameraDevice))).scalars().all()
    mods_by_id = {c.id: c.get_modules() for c in cams}
    refreshed = 0
    for inst in list(camera_manager.instances.values()):
        if inst.cam_id in mods_by_id:
            inst.modules = mods_by_id[inst.cam_id]
            refreshed += 1

    return {
        "status": "ok" if queued else "perception_unavailable",
        "gallery_reloaded": queued,
        "anchors_loaded": len(photos),
        "auto_vectors_reseeded": len(seed),
        "cameras_refreshed": refreshed,
    }


@router.get("/shards")
async def shard_status(
    current_user: User = Depends(require_role(UserRole.admin, UserRole.supervisor)),
):
    """Report the perception sharding state: how many children, whether each is alive,
    which cameras it owns and how deep its queue is.

    This exists because a dead shard fails SILENTLY. The router keeps queueing to a
    queue nobody reads, so the cameras on that shard simply stop being processed - no
    exception, no error in any log, just an absence of alerts. Nothing else in the
    system reports it.

    Unsharded (the default) this reports one shard, which is the truth."""
    health = perception.shard_health()
    sizes = perception.queue_sizes()
    snap = perception._shard_map.snapshot() if perception._shard_map else {}
    by_shard = snap.get("by_shard") or {}
    loads = snap.get("loads") or []
    shards = []
    for h in health:
        i = h["shard"]
        shards.append({
            **h,
            "queue_depth": sizes[i] if i < len(sizes) else None,
            "queue_max": perception.MAX_QUEUE,
            "cameras": by_shard.get(i, []),
            # Estimated, not measured: the cost model that drives assignment. Compare it
            # against perf_debug.log rather than trusting it.
            "estimated_pct_core": round(loads[i] / 10.0, 1) if i < len(loads) else None,
        })
    total = sum(loads) if loads else 0.0
    return {
        "shards": len(health),
        "enabled": len(health) > 1,
        "all_alive": all(h["alive"] for h in health) if health else False,
        "estimated_total_pct_core": round(total / 10.0, 1),
        "estimated_ideal_pct_core": round(total / 10.0 / max(1, len(health)), 1),
        "detail": shards,
    }
