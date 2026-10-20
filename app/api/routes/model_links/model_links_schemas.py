import uuid

from pydantic import BaseModel, ConfigDict


class LinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project: str
    role: str
    placement: str
    url: str
    enabled: bool


class LinkUpdate(BaseModel):
    url: str = ""
    enabled: bool = False
