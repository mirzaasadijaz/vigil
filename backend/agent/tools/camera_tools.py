"""list_cameras. Deliberately does NOT return ip_address / username / password."""
from sqlalchemy import select

from backend.core.database import AsyncSessionLocal
from backend.models.camera import CameraDevice

SCHEMAS = [{
    "name": "list_cameras",
    "description": "List cameras (id, name, location, department, active). Use to map a camera "
                   "name or place to its id before filtering alerts.",
    "input_schema": {"type": "object", "properties": {}},
}]


async def list_cameras(_: dict) -> dict:
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(CameraDevice).order_by(CameraDevice.id))).scalars().all()
    return {"cameras": [{"id": c.id, "name": c.name, "location": c.location,
                         "department": c.department, "active": c.is_active} for c in rows]}


HANDLERS = {"list_cameras": list_cameras}
