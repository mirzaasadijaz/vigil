"""Run this once to create an admin user in the remote Laravel DB:
   conda run -n ppe python -m backend.scripts.create_admin
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from sqlalchemy import select
from backend.core.database import AsyncSessionLocal
from backend.core.security import hash_password
from backend.models.user import User, UserRole, TYPE_BY_ROLE


async def main():
    name = input("Admin name [Admin]: ").strip() or "Admin"
    email = input("Admin email: ").strip()
    password = input("Admin password [admin123]: ").strip() or "admin123"
    if not email:
        print("Email is required.")
        return

    async with AsyncSessionLocal() as session:
        existing = await session.execute(select(User).where(User.email == email.lower()))
        if existing.scalar_one_or_none():
            print(f"User '{email}' already exists.")
            return

        user = User(
            name=name,
            email=email.lower(),
            password=hash_password(password),
            type=TYPE_BY_ROLE[UserRole.admin],
            status="active",
            is_enable_login=1,
            created_by=1,
        )
        session.add(user)
        await session.commit()
        print(f"Admin user '{name}' ({email}) created successfully.")


if __name__ == "__main__":
    asyncio.run(main())
