from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from backend.core.database import get_db
from backend.core.dependencies import get_current_user, require_role
from backend.models.user import User, UserRole
from backend.models.camera import CameraDevice, NVRDevice
from backend.services.camera_manager import generate_frames, get_frame_bytes, camera_manager, DISABLED_BYTES
from backend.services.connection_manager import connection_manager

router = APIRouter(prefix="/stream", tags=["stream"])


async def _resolve_source(cam_id: int, db: AsyncSession):
    stmt = select(CameraDevice).options(
        selectinload(CameraDevice.nvr).selectinload(NVRDevice.rtsp_template)
    ).where(CameraDevice.id == cam_id)
    result = await db.execute(stmt)
    cam = result.scalar_one_or_none()
    if not cam:
        raise HTTPException(status_code=404, detail="Camera not found")
    return cam.get_rtsp_url(), cam.process_frame_rate or 10, cam.id, cam.get_modules(), cam.is_active


@router.get("/video")
async def video_feed(
    cam_id: int = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin, UserRole.supervisor, UserRole.operator)),
):
    source, frame_rate, db_id, modules, is_active = await _resolve_source(cam_id, db)
    if not is_active:
        # Disabled camera: never touch the source (avoids RTSP auth attempts / lockouts).
        return Response(content=DISABLED_BYTES, media_type="image/jpeg")
    return StreamingResponse(
        generate_frames(source, frame_rate, db_id, modules),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@router.get("/snapshot")
async def snapshot(
    cam_id: int = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin, UserRole.supervisor, UserRole.operator)),
):
    source, frame_rate, db_id, modules, is_active = await _resolve_source(cam_id, db)
    if not is_active:
        return Response(content=DISABLED_BYTES, media_type="image/jpeg")
    data = get_frame_bytes(source, frame_rate, db_id, modules)
    return Response(content=data, media_type="image/jpeg")


@router.websocket("/ws/{cam_id}")
async def ws_camera(
    cam_id: int,
    ws: WebSocket,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    source, frame_rate, db_id, modules, is_active = await _resolve_source(cam_id, db)
    cam_key = str(cam_id)
    await connection_manager.connect(cam_key, ws)
    if is_active:
        instance = camera_manager.get_instance(source, frame_rate, db_id, modules)
        instance.start()
    else:
        # Disabled camera: show a static "DISABLED" frame, never open the RTSP source.
        try:
            await ws.send_bytes(DISABLED_BYTES)
        except Exception:
            pass
    try:
        while True:
            # Keeps connection alive; raises WebSocketDisconnect when client leaves
            await ws.receive_text()
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        await connection_manager.disconnect(cam_key, ws)
