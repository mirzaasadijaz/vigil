from pydantic import BaseModel


class SettingsRead(BaseModel):
    office_open_time: str
    office_close_time: str


class SettingsUpdate(BaseModel):
    office_open_time: str
    office_close_time: str
