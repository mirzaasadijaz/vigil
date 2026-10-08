from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from backend.models.user import UserRole


class UserCreate(BaseModel):
    name: str
    email: str
    password: str
    role: UserRole = UserRole.viewer


class UserUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    password: Optional[str] = None
    role: Optional[UserRole] = None
    is_active: Optional[bool] = None


class UserRead(BaseModel):
    id: int
    name: str
    email: Optional[str]
    role: UserRole
    is_active: bool
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}
