from fastapi import Depends, HTTPException, status
from starlette.requests import HTTPConnection
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
from .database import get_db
from .security import decode_token
from backend.models.user import User, UserRole
import time

# Auth user cache: the remote DB is slow (~200ms+/query), so cache the current
# user row briefly. Column attributes only — safe on detached instances.
_USER_CACHE: dict[int, tuple[float, User]] = {}
_USER_CACHE_TTL = 60.0


async def get_current_user(request: HTTPConnection, db: AsyncSession = Depends(get_db)) -> User:
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    payload = decode_token(token, token_type="access")
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")
    uid = int(user_id)
    cached = _USER_CACHE.get(uid)
    if cached and (time.monotonic() - cached[0]) < _USER_CACHE_TTL:
        return cached[1]
    result = await db.execute(select(User).where(User.id == uid))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")
    _USER_CACHE[uid] = (time.monotonic(), user)
    return user


def require_role(*roles: UserRole):
    async def check(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied. Required roles: {[r.value for r in roles]}",
            )
        return current_user
    return check


# Shorthand dependency factories
def admin_only():
    return require_role(UserRole.admin)

def admin_or_supervisor():
    return require_role(UserRole.admin, UserRole.supervisor)

def operator_and_above():
    return require_role(UserRole.admin, UserRole.supervisor, UserRole.operator)

def any_authenticated():
    return get_current_user
