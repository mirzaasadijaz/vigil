from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.core.database import get_db
from backend.core.dependencies import get_current_user, require_role
from backend.models.user import User, UserRole
from backend.models.camera import RTSPStringTemplate
from backend.schemas.camera import RTSPTemplateCreate, RTSPTemplateRead

router = APIRouter(prefix="/rtsp-templates", tags=["rtsp-templates"])


@router.get("/")
async def list_templates(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(RTSPStringTemplate).order_by(RTSPStringTemplate.name))
    templates = result.scalars().all()
    return [{"id": t.id, "name": t.name, "template_string": t.template_string} for t in templates]


@router.post("/")
async def create_template(
    data: RTSPTemplateCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    t = RTSPStringTemplate(**data.model_dump())
    db.add(t)
    await db.commit()
    return {"status": "success"}


@router.put("/{template_id}")
async def update_template(
    template_id: int,
    data: RTSPTemplateCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    result = await db.execute(select(RTSPStringTemplate).where(RTSPStringTemplate.id == template_id))
    t = result.scalar_one_or_none()
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")
    t.name = data.name
    t.template_string = data.template_string
    await db.commit()
    return {"status": "success"}


@router.delete("/{template_id}")
async def delete_template(
    template_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    result = await db.execute(select(RTSPStringTemplate).where(RTSPStringTemplate.id == template_id))
    t = result.scalar_one_or_none()
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")
    await db.delete(t)
    await db.commit()
    return {"status": "success"}
