"""Source DTOs."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    adapter_class: str
    config: dict[str, Any]
    enabled: bool
    priority: int
    created_at: datetime
    updated_at: datetime
