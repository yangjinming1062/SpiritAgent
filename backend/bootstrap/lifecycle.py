"""进程生命周期：启动顺序（迁移→配置水合→调度/事件/渠道→任务恢复）与停机顺序。"""

import asyncio
import logging
from collections.abc import AsyncGenerator, Awaitable
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path

from alembic import command
from alembic.config import Config
from entrypoints.scheduler.cron import start_scheduler
from fastapi import FastAPI
from features.accounts.queries import list_user_ids
from features.assets.lifecycle import cleanup_user_assets
from features.configuration.system_settings import load_and_apply_system_settings
from features.media.video_jobs import cleanup_user_video_jobs
from features.media.video_queue.service import cleanup_video_queue
from features.posts.publication import gc_autonomous_publications
from infrastructure.ai.providers.http import aclose_all
from infrastructure.async_io import run_blocking
from infrastructure.attachments import attachment_root
from infrastructure.config import insecure_secret_settings
from infrastructure.database import ENGINE, SESSION_LOCAL, database_url
from infrastructure.logger import get_logger, setup_logging
from infrastructure.temp_files import cleanup_expired
from infrastructure.web.web_providers import aclose as aclose_web_providers
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
    config_path = Path(__file__).parents[1] / "alembic.ini"
    cfg = Config()
    cfg.file_config["DEFAULT"]["here"] = config_path.parent.as_posix()
    cfg.file_config.read(config_path, encoding="utf-8")
    # 跳过 fileConfig，否则 alembic.ini 的 WARNING root 会接管全局日志、禁用已建 logger。
    cfg.attributes["configure_logger"] = False
    cfg.set_main_option("sqlalchemy.url", database_url("postgresql+psycopg").replace("%", "%%"))
    command.upgrade(cfg, "head")


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
            await cleanup_user_assets(user_id, discover=discover)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    cleanup_task = BackgroundTask("backend.temp-file-cleanup")
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
                await _best_effort(
                    "formal assets",
                    _cleanup_formal_assets(discover=minute % 60 == 0),
                    level=logging.WARNING,
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
        await cleanup_task.stop()

        # 先停所有任务生产入口再 drain，避免收敛期间派生的新任务逃过快照。
        await RUNTIME_LIFECYCLE.stop_producers()

        # 释放引擎前先 drain 模块级任务集合，避免 SIGTERM 把持有连接池的协程留在 commit 中途。
        await _best_effort("runtime tasks", RUNTIME_LIFECYCLE.drain())

        await _best_effort("database engine", ENGINE.dispose())
        await _best_effort("web providers", aclose_web_providers())
        await _best_effort(
            "LLM clients",
            asyncio.wait_for(aclose_all(), timeout=_LLM_CLIENT_CLOSE_TIMEOUT_SECONDS),
        )
