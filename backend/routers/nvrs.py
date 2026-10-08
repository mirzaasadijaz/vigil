from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from backend.core.database import get_db
from backend.core.dependencies import get_current_user, require_role
from backend.models.user import User, UserRole
from backend.models.camera import NVRDevice, CameraDevice, RTSPStringTemplate
from backend.schemas.camera import NVRCreate, NVRRead

router = APIRouter(prefix="/nvrs", tags=["nvrs"])


@router.get("/")
async def list_nvrs(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(
        select(NVRDevice)
        .options(selectinload(NVRDevice.channels), selectinload(NVRDevice.rtsp_template))
        .order_by(NVRDevice.id)
    )
    nvrs = result.scalars().all()
    data = []
    for n in nvrs:
        channels = [{"id": c.id, "name": c.name, "channel": c.channel, "fps": c.process_frame_rate, "module_config": c.get_modules()} for c in (n.channels or [])]
        data.append({
            "id": n.id, "name": n.name, "ip_address": n.ip_address, "port": n.port,
            "username": n.username, "password": n.password,
            "rtsp_template_id": n.rtsp_template_id, "channels": channels,
        })
    return data


@router.post("/")
async def create_nvr(
    nvr_data: NVRCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    d = nvr_data.model_dump()
    rtsp_template_id = d.pop("rtsp_template", None)
    nvr = NVRDevice(**d, rtsp_template_id=rtsp_template_id)
    db.add(nvr)
    await db.commit()
    await db.refresh(nvr)
    return {"status": "success", "id": nvr.id}


@router.put("/{nvr_id}")
async def update_nvr(
    nvr_id: int,
    nvr_data: NVRCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    result = await db.execute(select(NVRDevice).where(NVRDevice.id == nvr_id))
    nvr = result.scalar_one_or_none()
    if not nvr:
        raise HTTPException(status_code=404, detail="NVR not found")
    d = nvr_data.model_dump()
    nvr.rtsp_template_id = d.pop("rtsp_template", None)
    for k, v in d.items():
        if k == "password" and not v:
            continue   # blank password = keep existing (don't wipe)
        setattr(nvr, k, v)
    await db.commit()
    return {"status": "success"}


@router.delete("/{nvr_id}")
async def delete_nvr(
    nvr_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    from backend.models.alert import AlertLog
    from backend.models.mess import MessSession, MessSessionItem
    from sqlalchemy import delete
    result = await db.execute(
        select(NVRDevice).options(selectinload(NVRDevice.channels)).where(NVRDevice.id == nvr_id)
    )
    nvr = result.scalar_one_or_none()
    if not nvr:
        raise HTTPException(status_code=404, detail="NVR not found")
    cam_ids = [c.id for c in nvr.channels]
    if cam_ids:
        session_result = await db.execute(
            select(MessSession.id).where(MessSession.camera_id.in_(cam_ids))
        )
        session_ids = session_result.scalars().all()
        if session_ids:
            await db.execute(delete(MessSessionItem).where(MessSessionItem.session_id.in_(session_ids)))
            await db.execute(delete(MessSession).where(MessSession.id.in_(session_ids)))
        await db.execute(delete(AlertLog).where(AlertLog.camera_id.in_(cam_ids)))
    await db.delete(nvr)
    await db.commit()
    return {"status": "success"}


@router.post("/{nvr_id}/channels/")
async def add_channel(
    nvr_id: int,
    channel: int,
    process_frame_rate: int = 10,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    result = await db.execute(select(NVRDevice).options(selectinload(NVRDevice.rtsp_template)).where(NVRDevice.id == nvr_id))
    nvr = result.scalar_one_or_none()
    if not nvr:
        raise HTTPException(status_code=404, detail="NVR not found")
    existing = await db.execute(select(CameraDevice).where(CameraDevice.nvr_id == nvr_id, CameraDevice.channel == channel))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Channel already exists")
    cam = CameraDevice(name=f"{nvr.name} - CH {channel}", channel=channel, process_frame_rate=process_frame_rate, nvr_id=nvr_id)
    db.add(cam)
    await db.commit()
    return {"status": "success"}


@router.post("/{nvr_id}/channels/bulk")
async def add_channels_bulk(
    nvr_id: int,
    payload: dict = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    """Add several channels to an NVR at once. Body: {channels: int[], process_frame_rate?: int}.
    Skips channels that already exist. Returns counts."""
    channels = payload.get("channels") or []
    try:
        fps = int(payload.get("process_frame_rate") or 10)
    except (TypeError, ValueError):
        fps = 10
    channels = sorted({int(c) for c in channels if c is not None})
    if not channels:
        raise HTTPException(status_code=400, detail="channels is required")
    result = await db.execute(select(NVRDevice).where(NVRDevice.id == nvr_id))
    nvr = result.scalar_one_or_none()
    if not nvr:
        raise HTTPException(status_code=404, detail="NVR not found")
    existing = await db.execute(select(CameraDevice.channel).where(CameraDevice.nvr_id == nvr_id))
    have = {row[0] for row in existing.all()}
    added = 0
    for ch in channels:
        if ch in have:
            continue
        db.add(CameraDevice(name=f"{nvr.name} - CH {ch}", channel=ch, process_frame_rate=fps, nvr_id=nvr_id))
        have.add(ch)
        added += 1
    await db.commit()
    return {"status": "success", "added": added, "skipped": len(channels) - added}


@router.delete("/{nvr_id}/channels/{cam_id}")
async def delete_channel(
    nvr_id: int,
    cam_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    result = await db.execute(select(CameraDevice).where(CameraDevice.id == cam_id, CameraDevice.nvr_id == nvr_id))
    cam = result.scalar_one_or_none()
    if not cam:
        raise HTTPException(status_code=404, detail="Channel not found")
    await db.delete(cam)
    await db.commit()
    return {"status": "success"}
