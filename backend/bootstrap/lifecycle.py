"""进程生命周期：启动顺序（迁移→配置水合→调度/事件/渠道→任务恢复）与停机顺序。"""

import asyncio
import contextlib
from collections.abc import AsyncGenerator, Awaitable
from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from components import (
    ENGINE,
    SESSION_LOCAL,
    attachment_root,
    begin_user_request,
    cleanup_expired,
    database_url,
    drain_user_tasks,
    end_user_request,
    get_logger,
    insecure_secret_settings,
    setup_logging,
)
from fastapi import FastAPI
from modules.auth import User
from services.adapters.desktop import drain as drain_user_sessions
from services.adapters.scheduler import drain as drain_cron
from services.adapters.scheduler import start_scheduler, stop_scheduler
from services.application.actions import (
    drain_desktop_reviews,
    drain_proposal_reviews,
    resume_desktop_reviews,
    resume_proposal_reviews,
)
from services.application.chat import recover_interrupted_submissions
from services.application.configuration import load_and_apply_system_settings
from services.application.generation import (
    cleanup_user_video_jobs,
    drain_character_extractions,
    drain_desktop_video_jobs,
    drain_outfit_descriptions,
    drain_scene_jobs,
    drain_video_jobs,
    drain_video_pack_generation,
    recover_outfit_descriptions,
    resume_character_extractions,
    resume_desktop_video_jobs,
    resume_initial_appearance,
    resume_pending_video_jobs,
    resume_scene_jobs,
    resume_video_generation_jobs,
)
from services.application.posts import (
    drain_autonomous_posts,
    drain_publications,
    drain_replies,
    gc_autonomous_publications,
    resume_publications,
    resume_replies,
)
from services.domains.actions import cleanup_retired_action_assets
from services.domains.assets import cleanup_assets
from services.domains.companion import drain_first_greeting, drain_persona_background
from services.domains.update_releases import recover_update_storage
from services.infrastructure.event_store import drain_event_tasks, start_event_loop, stop_event_loop
from services.infrastructure.llm import aclose_all
from services.infrastructure.web import aclose as aclose_web_providers
from sqlalchemy import select

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


async def _drain_runtime_tasks() -> None:
    """按模块集中等待后台任务，并保留每个 drain 的失败诊断。"""
    steps: tuple[tuple[str, Awaitable[object]], ...] = (
        ("cron", drain_cron()),
        ("posts_autonomous", drain_autonomous_posts()),
        ("post_publications", drain_publications()),
        ("post_replies", drain_replies()),
        ("first_greeting", drain_first_greeting()),
        ("persona_background", drain_persona_background()),
        ("character_extractions", drain_character_extractions()),
        ("outfit_descriptions", drain_outfit_descriptions()),
        ("scene_jobs", drain_scene_jobs()),
        ("video_jobs", drain_video_jobs()),
        ("video_pack_generation", drain_video_pack_generation()),
        ("proposal_reviews", drain_proposal_reviews()),
        ("desktop_reviews", drain_desktop_reviews()),
        ("desktop_video_jobs", drain_desktop_video_jobs()),
        ("event_tasks", drain_event_tasks()),
        ("user_sessions", drain_user_sessions()),
        ("user_tasks", drain_user_tasks()),
    )
    results = await asyncio.gather(*(operation for _, operation in steps), return_exceptions=True)
    for (step, _), result in zip(steps, results, strict=True):
        if isinstance(result, BaseException):
            logger.error("Backend task drain failed", extra={"step": step}, exc_info=result)


def _run_migrations() -> None:
    """升级到最新 Alembic 版本。"""
    cfg = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    # 跳过 fileConfig，否则 alembic.ini 的 WARNING root 会接管全局日志、禁用已建 logger。
    cfg.attributes["configure_logger"] = False
    cfg.set_main_option("sqlalchemy.url", database_url("postgresql+psycopg").replace("%", "%%"))
    command.upgrade(cfg, "head")


async def _cleanup_formal_assets(*, discover: bool) -> None:
    async with SESSION_LOCAL() as db:
        users = list(await db.scalars(select(User.id)))
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
        if insecure := insecure_secret_settings():
            raise RuntimeError(f"Set non-empty, non-example values for: {', '.join(insecure)}.")
        await asyncio.to_thread(_run_migrations)
        async with SESSION_LOCAL() as session:
            await load_and_apply_system_settings(session)

        attachment_root().mkdir(parents=True, exist_ok=True)
        await recover_update_storage()
        await recover_outfit_descriptions()
        await cleanup_retired_action_assets()
        await gc_autonomous_publications()

        await recover_interrupted_submissions()
        start_scheduler()
        # LISTEN 专线：event_store 内部直连 + 断线 5s 重连；asyncpg 只接受纯 postgresql:// URL。
        start_event_loop(database_url("postgresql"))
        await resume_pending_video_jobs()
        # 视频包凭持久化句柄续跑，不重复提交付费任务；中断的上传导入包按失败落库并广播。
        await resume_video_generation_jobs()
        await resume_proposal_reviews()  # pending 评审重新调度（approve 后自动接生成编排）。
        await resume_desktop_video_jobs()
        # 启动只恢复已持久化的评审状态；评审通过后由用户或维护恢复显式启动付费制作。
        await resume_desktop_reviews(allow_generation=False)
        await resume_character_extractions()
        await resume_scene_jobs()
        await resume_initial_appearance()
        await resume_publications()
        await resume_replies()
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
        await _best_effort_shutdown("scheduler", stop_scheduler())
        await _best_effort_shutdown("event loop", stop_event_loop())

        # 释放引擎前先 drain 模块级任务集合，避免 SIGTERM 把持有连接池的协程留在 commit 中途。
        await _best_effort_shutdown("runtime tasks", _drain_runtime_tasks())

        await _best_effort_shutdown("database engine", ENGINE.dispose())
        await _best_effort_shutdown("web providers", aclose_web_providers())
        await _best_effort_shutdown(
            "LLM clients",
            asyncio.wait_for(aclose_all(), timeout=_LLM_CLIENT_CLOSE_TIMEOUT_SECONDS),
        )
