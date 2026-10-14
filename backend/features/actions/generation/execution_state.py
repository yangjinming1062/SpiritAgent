"""动作制作的进程内任务集合与固定规格；持久状态由动作记录拥有。"""

import asyncio

from infrastructure.logger import get_logger
from runtime.background import TaskBag

logger = get_logger(__name__)


_DEFAULT_CANVAS = (512, 768)


_SYSTEM_ACTION_SECONDS = 2.0


_ACTION_PIPELINE_CONCURRENCY = 2


_SOURCE_EXT_BY_MIME = {
    "image/png": ".png",
    "image/webp": ".webp",
    "video/webm": ".webm",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/x-matroska": ".mkv",
}


_SOURCE_MEDIA_EXTS = {"mp4", "webm", "mov", "mkv"}


_TASKS = TaskBag("companion.video")


_GEN_INFLIGHT: set[int] = set()


_GEN_TASK_COUNTS: dict[int, int] = {}


_PACK_GEN_INFLIGHT: set[int] = set()


_PACK_GEN_PENDING: set[int] = set()


_ACTION_INFLIGHT: set[int] = set()


_ACTION_PIPELINE_SEMAPHORES: dict[int, asyncio.Semaphore] = {}


_PACK_FINALIZE_LOCKS: dict[int, asyncio.Lock] = {}


def _action_pipeline_semaphore(pack_id: int) -> asyncio.Semaphore:
    return _ACTION_PIPELINE_SEMAPHORES.setdefault(pack_id, asyncio.Semaphore(_ACTION_PIPELINE_CONCURRENCY))


def _pack_finalize_lock(pack_id: int) -> asyncio.Lock:
    return _PACK_FINALIZE_LOCKS.setdefault(pack_id, asyncio.Lock())
