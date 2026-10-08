from pydantic import BaseModel
from typing import Optional, List


class RTSPTemplateRead(BaseModel):
    id: int
    name: str
    template_string: str

    model_config = {"from_attributes": True}


class RTSPTemplateCreate(BaseModel):
    name: str
    template_string: str


class ChannelRead(BaseModel):
    id: int
    name: str
    channel: int
    fps: int

    model_config = {"from_attributes": True}


class NVRRead(BaseModel):
    id: int
    name: str
    ip_address: str
    port: int
    username: Optional[str]
    password: Optional[str]
    rtsp_template_id: Optional[int]
    channels: List[ChannelRead] = []

    model_config = {"from_attributes": True}


class NVRCreate(BaseModel):
    name: str
    ip_address: str
    port: int = 554
    username: Optional[str] = None
    password: Optional[str] = None
    rtsp_template: Optional[int] = None


class CameraRead(BaseModel):
    id: int
    name: str
    ip: Optional[str]
    channel: int
    fps: int
    active: bool
    username: Optional[str]
    password: Optional[str]


class CameraCreate(BaseModel):
    name: str
    ip_address: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    channel: int = 102
    process_frame_rate: int = 10
    location: Optional[str] = None
    department: Optional[str] = None
    module_config: Optional[list] = None
    nvr_id: Optional[int] = None


class CameraUpdate(BaseModel):
    name: Optional[str] = None
    ip_address: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    channel: Optional[int] = None
    process_frame_rate: Optional[int] = None
    location: Optional[str] = None
    department: Optional[str] = None
    module_config: Optional[list] = None
