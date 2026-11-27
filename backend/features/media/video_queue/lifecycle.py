"""队列先恢复远端占用，再开放各业务的制作恢复。"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from infrastructure.async_io import wait_for_task
from runtime.lifecycle import RuntimeParticipant

from .service import drain_video_queue, prepare_video_queue, start_video_queue, stop_video_queue


@asynccontextmanager
async def video_queue_lifespan() -> AsyncIterator[None]:
    """单实例锁覆盖共享目录清理、媒体恢复及所有业务任务的停机收尾。"""
    await prepare_video_queue()
    try:
        yield
    finally:
        await wait_for_task(asyncio.create_task(drain_video_queue()))


def runtime_participants() -> tuple[RuntimeParticipant, ...]:
    return (RuntimeParticipant("media.video_queue", recover=start_video_queue, stop_producer=stop_video_queue),)
