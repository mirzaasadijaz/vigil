from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class AlertRead(BaseModel):
    id: int
    name: str
    violation: str
    timestamp: str
    snapshot_url: Optional[str]

    model_config = {"from_attributes": True}
