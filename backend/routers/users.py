from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.core.database import get_db
from backend.core.dependencies import require_role
from backend.core.security import hash_password
from backend.models.user import User, UserRole, TYPE_BY_ROLE
from backend.schemas.user import UserCreate, UserUpdate, UserRead

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/", response_model=list[UserRead])
async def list_users(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    result = await db.execute(select(User).order_by(User.id))
    return result.scalars().all()


@router.post("/", response_model=UserRead)
async def create_user(
    data: UserCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    email = data.email.lower().strip()
    existing = await db.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already taken")
    user = User(
        name=data.name,
        email=email,
        password=hash_password(data.password),
        type=TYPE_BY_ROLE.get(data.role, "employee"),
        status="active",
        is_enable_login=1,
        created_by=current_user.id,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.put("/{user_id}", response_model=UserRead)
async def update_user(
    user_id: int,
    data: UserUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if data.name is not None:
        user.name = data.name
    if data.email is not None:
        email = data.email.lower().strip()
        dup = await db.execute(select(User).where(User.email == email, User.id != user_id))
        if dup.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Email already taken")
        user.email = email
    if data.password is not None:
        user.password = hash_password(data.password)
    if data.role is not None:
        user.type = TYPE_BY_ROLE.get(data.role, user.type)
    if data.is_active is not None:
        user.status = "active" if data.is_active else "inactive"
        user.is_enable_login = 1 if data.is_active else 0
    await db.commit()
    await db.refresh(user)
    return user


@router.delete("/{user_id}")
async def delete_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    from backend.services import hr_index
    if hr_index.by_user_id(user_id):
        raise HTTPException(status_code=400, detail="User is linked to an employee record; remove the employee first")
    await db.delete(user)
    await db.commit()
    return {"status": "success"}
