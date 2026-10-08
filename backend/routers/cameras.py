import json
import socket
import ping3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from backend.core.database import get_db
from backend.core.dependencies import get_current_user, require_role, admin_only
from backend.models.user import User, UserRole
from backend.models.alert import AlertLog
from backend.models.camera import CameraDevice, NVRDevice
from backend.schemas.camera import CameraRead, CameraCreate, CameraUpdate
from backend import services

router = APIRouter(prefix="/cameras", tags=["cameras"])

TZ = ZoneInfo("Asia/Karachi")


def _check_camera_active(cam: CameraDevice) -> bool:
    source = cam.get_rtsp_url()
    mgr = services.camera_manager
    with mgr.lock:
        if source in mgr.instances:
            inst = mgr.instances[source]
            if not inst.stopped and inst.error_count < 10:
                return True
    if cam.ip_address:
        try:
            with socket.create_connection((cam.ip_address, 554), timeout=0.5):
                return True
        except Exception:
            try:
                delay = ping3.ping(cam.ip_address, timeout=0.5)
                return delay is not None and delay is not False
            except Exception:
                pass
    return False


@router.get("/")
async def list_cameras(
    ip_only: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    stmt = select(CameraDevice).options(selectinload(CameraDevice.nvr).selectinload(NVRDevice.rtsp_template))
    if ip_only:
        stmt = stmt.where(CameraDevice.nvr_id.is_(None))
    result = await db.execute(stmt.order_by(CameraDevice.id))
    cameras = result.scalars().all()

    data = []
    for c in cameras:
        is_active = _check_camera_active(c)
        data.append({
            "id": c.id,
            "name": c.name,
            "ip_address": c.ip_address,
            "channel": c.channel,
            "process_frame_rate": c.process_frame_rate,
            "is_active": is_active,
            "username": c.username,
            "location": c.location,
            "department": c.department,
            "module_config": c.get_modules(),
            "nvr_id": c.nvr_id,
            "nvr_name": c.nvr.name if c.nvr else None,
            # Legacy aliases
            "ip": c.ip_address or f"NVR (CH {c.channel})",
            "fps": c.process_frame_rate,
            "active": is_active,
        })
    return data


@router.get("/counts")
async def module_counts(
    module: str,
    today: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Per-camera IN / OUT / total for a directional crossing module.

    Rebuilt from `alert_logs` rather than stored anywhere: the unified event store
    already holds one row per crossing, so a separate counter would be a second source
    of truth that can disagree with the events it is supposed to summarise. This is the
    same roll-up `seed_counts` performs at startup to restore the live consumers.

    `today=true` clips to the current day in Asia/Karachi, which is what Truck Count
    wants - it resets daily, and the view would otherwise show a lifetime total that
    never matches the number on the live overlay.

    Declared BEFORE any `/{cam_id}` route would be: FastAPI matches in definition
    order, and a path parameter added above this would swallow "counts".
    """
    stmt = (
        select(AlertLog.camera_id, AlertLog.event_type, func.count())
        .where(AlertLog.module == module,
               AlertLog.event_type.in_(("entry", "exit")))
        .group_by(AlertLog.camera_id, AlertLog.event_type)
    )
    if today:
        start = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
        stmt = stmt.where(
            AlertLog.timestamp >= start.astimezone(timezone.utc).replace(tzinfo=None))

    rows = (await db.execute(stmt)).all()
    out: dict[int, dict] = {}
    for cam, event_type, n in rows:
        if cam is None:
            continue
        row = out.setdefault(int(cam),
                             {"camera_id": int(cam), "in": 0, "out": 0, "total": 0})
        row["in" if event_type == "entry" else "out"] = int(n)
    for row in out.values():
        row["total"] = row["in"] + row["out"]
    return list(out.values())


@router.post("/bulk-modules")
async def bulk_modules(
    payload: dict = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    """Apply one or more module assignments to many cameras at once (IP cameras + NVR channels).
    Replaces those same modules on each camera and keeps its other modules.
    Accepts either `assignments` (list) or a single `assignment`."""
    ids = payload.get("camera_ids") or []
    assignments = payload.get("assignments")
    if assignments is None:
        single = payload.get("assignment")
        assignments = [single] if single else []
    assignments = [a for a in assignments if a and a.get("module")]
    if not ids or not assignments:
        raise HTTPException(status_code=400, detail="camera_ids and at least one module assignment are required")
    incoming = {a["module"] for a in assignments}
    result = await db.execute(select(CameraDevice).where(CameraDevice.id.in_(ids)))
    cams = result.scalars().all()
    for cam in cams:
        mods = [m for m in cam.get_modules() if m.get("module") not in incoming]
        mods.extend(assignments)
        cam.module_config = json.dumps(mods)
    await db.commit()
    return {"status": "success", "updated": len(cams), "modules": len(assignments)}


@router.post("/")
async def create_camera(
    cam_data: CameraCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    payload = cam_data.model_dump()
    mc = payload.pop("module_config", None)
    cam = CameraDevice(**payload)
    if mc is not None:
        cam.module_config = json.dumps(mc)
    db.add(cam)
    await db.commit()
    await db.refresh(cam)
    return {"status": "success", "id": cam.id}


@router.put("/{cam_id}")
async def update_camera(
    cam_id: int,
    cam_data: CameraUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    result = await db.execute(select(CameraDevice).where(CameraDevice.id == cam_id))
    cam = result.scalar_one_or_none()
    if not cam:
        raise HTTPException(status_code=404, detail="Camera not found")
    mod_updated = False
    for field, val in cam_data.model_dump(exclude_unset=True).items():
        if field == "module_config":
            cam.module_config = json.dumps(val) if val is not None else None
            mod_updated = True
        else:
            setattr(cam, field, val)
    await db.commit()
    # Push updated modules to the running CameraInstance immediately
    if mod_updated:
        services.camera_manager.refresh_modules_by_cam_id(cam_id, cam.get_modules())
    return {"status": "success"}


@router.delete("/{cam_id}")
async def delete_camera(
    cam_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    result = await db.execute(select(CameraDevice).where(CameraDevice.id == cam_id))
    cam = result.scalar_one_or_none()
    if not cam:
        raise HTTPException(status_code=404, detail="Camera not found")
    await db.delete(cam)
    await db.commit()
    # Drop it from the perception routing table too, so a deleted camera's estimated
    # cost stops counting against its shard when the next camera is placed. No-op
    # unless sharding is enabled.
    try:
        from backend.services import perception
        perception.forget_camera(cam_id)
    except Exception:
        pass
    return {"status": "success"}
