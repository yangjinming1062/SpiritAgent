"""跨业务读取桌面生活运行态的公开快照。"""

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from .repository import desktop_state


class DesktopStateSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)

    user_id: int
    current_set_id: int | None
    selected_action_id: int | None
    selected_play_id: str | None
    loop_action_id: int | None
    set_epoch: int
    version: int
    pinned: bool
    autonomous_enabled: bool
    preparation_error: str | None


async def desktop_state_snapshot(db: AsyncSession, user_id: int) -> DesktopStateSnapshot | None:
    row = await desktop_state(db, user_id)
    return DesktopStateSnapshot.model_validate(row) if row is not None else None
