"""进程生命周期：启动顺序（迁移→配置水合→调度/事件/渠道→任务恢复）与停机顺序。"""

import asyncio
import logging
from collections.abc import AsyncGenerator, Awaitable, Iterator
from contextlib import asynccontextmanager, contextmanager
from functools import partial
from pathlib import Path

from alembic import command
from alembic.config import Config
from entrypoints.scheduler.cron import start_scheduler
from fastapi import FastAPI
from features.accounts.queries import list_user_ids
from features.assets.lifecycle import cleanup_user_assets
from features.configuration.system_settings import load_and_apply_system_settings
from features.media.continuations import reconcile_media_waits
from features.media.image_jobs import cleanup_user_image_jobs, recover_image_jobs
from features.media.manual_reviews import recover_manual_operations
from features.media.video_jobs import cleanup_user_video_jobs, resume_pending_video_jobs
from features.media.video_queue.lifecycle import video_queue_lifespan
from features.media.video_queue.service import cleanup_video_queue
from features.posts.publication import gc_autonomous_publications
from features.realtime_voice.session import CALL_MANAGER
from filelock import FileLock, Timeout
from infrastructure.ai.providers.http import aclose_all
from infrastructure.async_io import run_blocking, wait_for_task
from infrastructure.attachments import attachment_root
from infrastructure.config import SETTINGS, insecure_secret_settings
from infrastructure.database import ENGINE, SESSION_LOCAL, database_url
from infrastructure.logger import get_logger, setup_logging
from infrastructure.temp_files import cleanup_expired, purge_media_work
from infrastructure.web.extract_provider import aclose_web_providers
from runtime.background import BackgroundTask
from runtime.events.loop import start_event_loop
from runtime.lifecycle import RUNTIME_LIFECYCLE
from runtime.user_maintenance import user_request_scope

from .runtime_lifecycle import configure_runtime_lifecycle

logger = get_logger(__name__)

# 连接池关闭要等在途请求释放；停机不能因个别泄漏的请求无限等待。
_LLM_CLIENT_CLOSE_TIMEOUT_SECONDS = 10.0


async def _best_effort(step: str, operation: Awaitable[object], *, level: int = logging.ERROR) -> None:
    """隔离维护和停机各步骤的失败，继续处理后续资源；取消仍向上传播。"""
    try:
        await operation
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.log(level, "Backend lifecycle step failed", extra={"step": step}, exc_info=True)


def _run_migrations() -> None:
    """升级到最新 Alembic 版本。"""
    cfg = Config(Path(__file__).parents[1] / "alembic.ini", attributes={"configure_logger": False})
    command.upgrade(cfg, "head")


@contextmanager
def _own_data_directory() -> Iterator[None]:
    """数据库重连会释放会话锁，数据卷的占用必须随进程独立收尾。"""
    directory = Path(SETTINGS.data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    try:
        lease = FileLock(directory / ".backend.lock").acquire(blocking=False)
    except Timeout as exc:
        raise RuntimeError("数据目录已由另一后端进程使用，请保持单实例部署") from exc
    with lease:
        yield


async def _cleanup_formal_assets(*, discover: bool) -> None:
    async with SESSION_LOCAL() as db:
        users = await list_user_ids(db)
    for user_id in users:
        async with user_request_scope(user_id) as acquired:
            if not acquired:
                continue
            try:
                await cleanup_user_video_jobs(user_id)
            except Exception:
                logger.warning("Abandoned video cleanup failed", extra={"user_id": user_id}, exc_info=True)
            await _best_effort("image jobs", cleanup_user_image_jobs(user_id), level=logging.WARNING)
            await _best_effort("image recovery", recover_image_jobs(user_id), level=logging.WARNING)
            await _best_effort("video recovery", resume_pending_video_jobs(user_id), level=logging.WARNING)
            await _best_effort("media continuations", reconcile_media_waits(user_id), level=logging.WARNING)
            await _best_effort("manual media operations", recover_manual_operations(user_id), level=logging.WARNING)
            await cleanup_user_assets(user_id, discover=discover)


@asynccontextmanager
async def _running_services() -> AsyncGenerator[None]:
    cleanup_task = BackgroundTask("backend.temp-file-cleanup")
    try:
        await RUNTIME_LIFECYCLE.prepare()
        start_scheduler()
        # LISTEN 专线直连 + 断线 5s 重连；asyncpg 只接受纯 postgresql:// URL。
        start_event_loop(database_url("postgresql"))
        await RUNTIME_LIFECYCLE.recover()
        await _cleanup_formal_assets(discover=True)

        async def _cleanup_loop() -> None:
            minute = 0
            while True:
                await asyncio.sleep(60)
                minute += 1
                await _best_effort(
                    "formal assets", _cleanup_formal_assets(discover=minute % 60 == 0), level=logging.WARNING
                )
                if minute % 60:
                    continue
                for step, operation in (
                    ("video queue", cleanup_video_queue),
                    ("temp files", partial(run_blocking, cleanup_expired)),
                    ("publications", gc_autonomous_publications),
                ):
                    await _best_effort(step, operation(), level=logging.WARNING)

        cleanup_task.start(_cleanup_loop())
        yield
    finally:
        await wait_for_task(asyncio.create_task(_stop_services(cleanup_task)))


async def _stop_services(cleanup_task: BackgroundTask) -> None:
    await cleanup_task.stop()
    # 先停所有任务生产入口再 drain，避免收敛期间派生的新任务逃过快照。
    await RUNTIME_LIFECYCLE.stop_producers()
    await _best_effort("realtime voice calls", CALL_MANAGER.end_all())
    await _best_effort("runtime tasks", RUNTIME_LIFECYCLE.drain())


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    setup_logging()
    try:
        if insecure := insecure_secret_settings():
            raise RuntimeError(f"Set non-empty, non-example values for: {', '.join(insecure)}.")
        with _own_data_directory():
            await run_blocking(_run_migrations)
            async with SESSION_LOCAL() as session:
                await load_and_apply_system_settings(session)
            attachment_root().mkdir(parents=True, exist_ok=True)
            configure_runtime_lifecycle()
            async with video_queue_lifespan():
                await run_blocking(purge_media_work)
                async with _running_services():
                    yield
    finally:
        await _best_effort("database engine", ENGINE.dispose())
        await _best_effort("web providers", aclose_web_providers())
        await _best_effort("LLM clients", asyncio.wait_for(aclose_all(), timeout=_LLM_CLIENT_CLOSE_TIMEOUT_SECONDS))
