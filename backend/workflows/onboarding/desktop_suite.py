"""屏幕尺寸、初始场景与着装就绪后建立一次默认桌景。"""

from features.desktop_life.service import ensure_current_desktop_videos
from infrastructure.logger import get_logger

logger = get_logger(__name__)


async def prepare_default_desktop_suite(user_id: int, *, allow_generation: bool = True) -> None:
    try:
        await ensure_current_desktop_videos(user_id, allow_generation=allow_generation)
    except Exception:
        logger.warning("default desktop suite preparation failed", extra={"user_id": user_id}, exc_info=True)
