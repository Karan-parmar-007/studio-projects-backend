from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class KeyOut(BaseModel):
    id: uuid.UUID
    name: str
    prefix: str
    created_at: datetime
    last_used_at: datetime | None


class KeyCreated(KeyOut):
    secret: str


class KeyCreate(BaseModel):
    name: str = Field(default="Default", max_length=80)
