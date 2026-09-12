"""进程生命周期：启动顺序（迁移→配置水合→调度/事件/渠道→任务恢复）与停机顺序。"""

import asyncio
import contextlib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from components import (
    ENGINE,
    SESSION_LOCAL,
    SETTINGS,
    attachment_root,
    cleanup_expired,
    get_logger,
    setup_logging,
)
from fastapi import FastAPI
from services.adapters.channels import start_channel_manager, stop_channel_manager
from services.adapters.desktop.handlers import drain as drain_user_sessions
from services.adapters.scheduler.cron import drain as drain_cron
from services.adapters.scheduler.cron import start_scheduler, stop_scheduler
from services.application.configuration.system_settings import load_and_apply_system_settings
from services.application.generation import (
    drain_room_backdrop_jobs,
    drain_video_jobs,
    recover_stuck_model_generations,
    resume_inflight_pipelines,
    resume_pending_video_jobs,
)
from services.domains.companion import drain_persona_background
from services.infrastructure.event_store import drain_event_tasks, start_event_loop, stop_event_loop
from services.infrastructure.llm import aclose_all
from services.infrastructure.web import aclose as aclose_web_providers
from sqlalchemy.engine import make_url

logger = get_logger(__name__)


def _sync_pg_url() -> str:
    return make_url(SETTINGS.database_url).set(drivername="postgresql+psycopg").render_as_string(hide_password=False)


def _raw_pg_dsn() -> str:
    # asyncpg 只接受不带 SQLAlchemy 驱动后缀的纯 postgresql:// URL。
    return make_url(SETTINGS.database_url).set(drivername="postgresql").render_as_string(hide_password=False)


def _run_migrations() -> None:
    """升级到最新 Alembic 版本；唯一一份 0001 baseline 已构建完整 schema。"""
    cfg = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    # 标记给 env.py，让启动迁移跳过 fileConfig；否则 alembic.ini 的 WARNING root 会接管全局日志、禁用所有已建 logger。
    cfg.attributes["configure_logger"] = False
    cfg.set_main_option("sqlalchemy.url", _sync_pg_url().replace("%", "%%"))
    command.upgrade(cfg, "head")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    setup_logging()
    if not SETTINGS.companion_asset_signing_key:
        raise RuntimeError("COMPANION_ASSET_SIGNING_KEY must be set.")
    await asyncio.to_thread(_run_migrations)
    async with SESSION_LOCAL() as session:
        await load_and_apply_system_settings(session)

    attachment_root(SETTINGS.data_dir).mkdir(parents=True, exist_ok=True)

    start_scheduler()
    # LISTEN 专线：event_store 内部直连 + 断线 5s 重连；cron 回合处理器已由 bootstrap/registrations 显式绑定。
    start_event_loop(_raw_pg_dsn())
    # IM 通道桥：拉起各用户已启用的渠道绑定，回合不依赖用户 WS。
    await start_channel_manager()
    await resume_pending_video_jobs()
    await recover_stuck_model_generations()
    # 3D 模型管道并入 web 后：从持久状态（companion_3d_models.status IN FLIGHT）重启尚未完成的 task。
    await resume_inflight_pipelines()

    async def _cleanup_loop():
        while True:
            await asyncio.sleep(3600)
            try:
                cleanup_expired()
            except Exception:
                logger.warning("Temp file cleanup failed", exc_info=True)

    cleanup_task = asyncio.create_task(_cleanup_loop())

    try:
        yield
    finally:
        cleanup_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cleanup_task

        # 先停调度器再 drain：tick 会往 cron 的模块级任务集合里 spawn 新 task，反过来的顺序留下一个能逃过 drain 的窗口。
        await stop_scheduler()

        # 释放引擎前先 drain 模块级任务集合；避免 SIGTERM 把持有连接池的协程留在 commit 中途。
        await asyncio.gather(
            drain_cron(),
            drain_persona_background(),
            drain_room_backdrop_jobs(),
            drain_video_jobs(),
            drain_event_tasks(),
            drain_user_sessions(),
            return_exceptions=True,
        )

        # IM 通道桥在 outbox 专线关闭前停稳：适配器任务可能还在写 WSEvent / 开 DB session。
        await stop_channel_manager()

        await stop_event_loop()

        await ENGINE.dispose()
        await aclose_web_providers()
        await aclose_all()
