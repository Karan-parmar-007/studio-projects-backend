from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DownloadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product: str
    platform: str
    filename: str
    content_type: str
    size: int
    version: str
    updated_at: datetime
