from datetime import datetime

from pydantic import BaseModel, ConfigDict


class UpdateVersionItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    version: str
    release_notes: str
    exe_filename: str
    exe_sha512: str
    exe_size: int
    mac_filename: str | None = None
    mac_sha512: str | None = None
    mac_size: int | None = None
    runner_filename: str | None = None
    runner_sha512: str | None = None
    runner_size: int | None = None
    runner_version: str | None = None
    is_active: bool
    created_at: datetime
    created_by: str | None


class UpdateVersionUpdate(BaseModel):
    release_notes: str | None = None
    is_active: bool | None = None


class UpdateVersionListResponse(BaseModel):
    items: list[UpdateVersionItem]
