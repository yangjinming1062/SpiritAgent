from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class NightlyActivityLogItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    target_date: date
    status: str
    summary: str | None = None
    payload: dict[str, Any] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class NightlyActivityLogListResponse(BaseModel):
    items: list[NightlyActivityLogItem]
