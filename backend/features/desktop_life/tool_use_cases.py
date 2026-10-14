"""桌面生活工具播放的事务用例。"""

from sqlalchemy.ext.asyncio import AsyncSession

from .schemas import DesktopVideoPlayCommand, DesktopVideoPlayRequest
from .service import play_desktop_action


async def play_action(
    db: AsyncSession,
    user_id: int,
    request: DesktopVideoPlayRequest,
    *,
    source: str,
) -> DesktopVideoPlayCommand:
    command = await play_desktop_action(db, user_id, request, source=source)
    await db.commit()
    return command
