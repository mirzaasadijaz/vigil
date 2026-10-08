from fastapi import APIRouter, Depends, HTTPException, Response, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import timedelta
from backend.core.database import get_db
from backend.core.security import verify_password, create_access_token, create_refresh_token, decode_token
from backend.core.dependencies import get_current_user
from backend.core.config import settings
from backend.models.user import User

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login")
async def login(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    form = await request.form()
    email = form.get("email", "").strip().lower()
    password = form.get("password", "")

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if not user or not user.password or not verify_password(password, user.password) or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    token_data = {"sub": str(user.id), "role": user.role.value}
    access_token = create_access_token(token_data)
    refresh_token = create_refresh_token(token_data)

    response.set_cookie("access_token", access_token, httponly=True, samesite="lax", max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60)
    response.set_cookie("refresh_token", refresh_token, httponly=True, samesite="lax", max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400)

    return {"status": "success", "role": user.role.value, "username": user.name, "name": user.name, "email": user.email}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie("access_token")
    response.delete_cookie("refresh_token")
    return {"status": "success"}


@router.post("/refresh")
async def refresh_token(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    token = request.cookies.get("refresh_token")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No refresh token")
    payload = decode_token(token, token_type="refresh")
    user_id = payload.get("sub")
    result = await db.execute(select(User).where(User.id == int(user_id)))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    new_token = create_access_token({"sub": str(user.id), "role": user.role.value})
    response.set_cookie("access_token", new_token, httponly=True, samesite="lax", max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60)
    return {"status": "success"}


@router.get("/me")
async def me(current_user: User = Depends(get_current_user)):
    return {
        "id": current_user.id,
        "username": current_user.name,
        "name": current_user.name,
        "email": current_user.email,
        "role": current_user.role.value,
        "is_active": current_user.is_active,
    }
