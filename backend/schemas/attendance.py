from pydantic import BaseModel
from typing import Optional


class AttendanceRead(BaseModel):
    employee_name: str
    employee_id: str
    date: str
    check_in: Optional[str]
    check_out: Optional[str]
    status: str
