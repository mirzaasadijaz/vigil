import asyncio
from fastapi import APIRouter, Depends, HTTPException
from backend.core.dependencies import get_current_user, require_role
from backend.models.user import User, UserRole
from backend.services import model_swapper

router = APIRouter(prefix="/mode", tags=["mode"])


@router.get("/")
async def get_mode(current_user: User = Depends(get_current_user)):
    return {"mode": model_swapper.get_system_mode()}


@router.post("/")
async def set_mode(
    mode: str,
    current_user: User = Depends(require_role(UserRole.admin, UserRole.supervisor)),
):
    if mode not in ("ppe", "face"):
        raise HTTPException(status_code=400, detail="Invalid mode. Use 'ppe' or 'face'.")
    # Run in thread pool so model loading doesn't block the async event loop
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, model_swapper.switch_mode, mode)
    return {"status": "success", "mode": model_swapper.get_system_mode()}
