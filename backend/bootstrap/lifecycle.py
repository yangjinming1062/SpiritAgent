"""进程生命周期：启动顺序（迁移→配置水合→调度/事件/渠道→任务恢复）与停机顺序。"""

import asyncio
import contextlib
from collections.abc import AsyncGenerator, Awaitable
from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from entrypoints.scheduler.cron import start_scheduler
from fastapi import FastAPI
from features.accounts.queries import list_user_ids
from features.actions.asset_retirement import cleanup_retired_action_assets
from features.assets.lifecycle import cleanup_assets
from features.configuration.system_settings import load_and_apply_system_settings
from features.media.video_jobs import cleanup_user_video_jobs
from features.posts.publication import gc_autonomous_publications
from infrastructure.ai.providers.http import aclose_all
from infrastructure.attachments import attachment_root
from infrastructure.config import insecure_secret_settings
from infrastructure.database import ENGINE, SESSION_LOCAL, database_url
from infrastructure.logger import get_logger, setup_logging
from infrastructure.temp_files import cleanup_expired
from infrastructure.web.web_providers import aclose as aclose_web_providers
from runtime.events.loop import start_event_loop
from runtime.lifecycle import RUNTIME_LIFECYCLE
from runtime.user_maintenance import begin_user_request, end_user_request

from .runtime_lifecycle import configure_runtime_lifecycle

logger = get_logger(__name__)

# 连接池关闭要等在途请求释放；停机不能因个别泄漏的请求无限等待。
_LLM_CLIENT_CLOSE_TIMEOUT_SECONDS = 10.0


async def _best_effort_shutdown(step: str, operation: Awaitable[object]) -> None:
    """记录单个停机步骤的失败并继续释放后续资源。"""
    try:
        await operation
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.error("Backend shutdown step failed", extra={"step": step}, exc_info=True)


def _run_migrations() -> None:
    """升级到最新 Alembic 版本。"""
    cfg = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    # 跳过 fileConfig，否则 alembic.ini 的 WARNING root 会接管全局日志、禁用已建 logger。
    cfg.attributes["configure_logger"] = False
    cfg.set_main_option("sqlalchemy.url", database_url("postgresql+psycopg").replace("%", "%%"))
    command.upgrade(cfg, "head")


async def _cleanup_formal_assets(*, discover: bool) -> None:
    async with SESSION_LOCAL() as db:
        users = await list_user_ids(db)
    for user_id in users:
        if not await begin_user_request(user_id):
            continue
        try:
            await cleanup_user_video_jobs(user_id)
        except Exception:
            logger.warning("Abandoned video cleanup failed", extra={"user_id": user_id}, exc_info=True)
        finally:
            await end_user_request(user_id)
    await cleanup_assets(discover=discover)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    cleanup_task: asyncio.Task[None] | None = None
    try:
        setup_logging()
        configure_runtime_lifecycle()
        if insecure := insecure_secret_settings():
            raise RuntimeError(f"Set non-empty, non-example values for: {', '.join(insecure)}.")
        await asyncio.to_thread(_run_migrations)
        async with SESSION_LOCAL() as session:
            await load_and_apply_system_settings(session)

        attachment_root().mkdir(parents=True, exist_ok=True)
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
                try:
                    await _cleanup_formal_assets(discover=minute % 60 == 0)
                except Exception:
                    logger.warning("Formal asset cleanup failed", exc_info=True)
                if minute % 60:
                    continue
                try:
                    await asyncio.to_thread(cleanup_expired)
                except Exception:
                    logger.warning("Temp file cleanup failed", exc_info=True)
                try:
                    await cleanup_retired_action_assets()
                except Exception:
                    logger.warning("Action asset cleanup failed", exc_info=True)
                try:
                    await gc_autonomous_publications()
                except Exception:
                    logger.warning("Publication cleanup failed", exc_info=True)

        cleanup_task = asyncio.create_task(_cleanup_loop(), name="backend.temp-file-cleanup")
        yield
    finally:
        if cleanup_task is not None:
            cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await cleanup_task

        # 先停所有任务生产入口再 drain，避免收敛期间派生的新任务逃过快照。
        await RUNTIME_LIFECYCLE.stop_producers()

        # 释放引擎前先 drain 模块级任务集合，避免 SIGTERM 把持有连接池的协程留在 commit 中途。
        await _best_effort_shutdown("runtime tasks", RUNTIME_LIFECYCLE.drain())

        await _best_effort_shutdown("database engine", ENGINE.dispose())
        await _best_effort_shutdown("web providers", aclose_web_providers())
        await _best_effort_shutdown(
            "LLM clients",
            asyncio.wait_for(aclose_all(), timeout=_LLM_CLIENT_CLOSE_TIMEOUT_SECONDS),
        )
