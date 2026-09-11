import asyncio
import contextlib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import services.chat.agent_delegate
import services.scheduler.cronjob_tool
import services.tools.builtin  # noqa: F401
from alembic import command
from alembic.config import Config
from api import ROUTERS
from components import (
    ENGINE,
    SESSION_LOCAL,
    SETTINGS,
    attachment_root,
    cleanup_expired,
    correlated_exception_response,
    correlation_id_middleware,
    get_logger,
    render_metrics_response,
    setup_logging,
)
from fastapi import FastAPI, Header, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from services.channels import start_channel_manager, stop_channel_manager
from services.companion import (
    drain_persona_background,
    drain_room_backdrop_jobs,
    recover_stuck_model_generations,
    resume_inflight_pipelines,
)
from services.gateway import drain_user_sessions, execute_cron_turn
from services.llm import aclose_all
from services.media import drain_video_jobs, resume_pending_video_jobs
from services.rate_limit import (
    limiter,
    rate_limit_exception_handler,
    stash_user_id_middleware,
)
from services.scheduler import drain_cron, start_scheduler, stop_scheduler
from services.system_settings import load_and_apply_system_settings
from services.tools import aclose
from services.ws import drain_cron_turns, start_ws_event_loop, stop_ws_event_loop
from slowapi.errors import RateLimitExceeded
from sqlalchemy.engine import make_url

logger = get_logger(__name__)


def _sync_pg_url() -> str:
    return make_url(SETTINGS.database_url).set(drivername="postgresql+psycopg").render_as_string(hide_password=False)


def _raw_pg_dsn() -> str:
    # asyncpg 只接受不带 SQLAlchemy 驱动后缀的纯 postgresql:// URL。
    return make_url(SETTINGS.database_url).set(drivername="postgresql").render_as_string(hide_password=False)


def _run_migrations() -> None:
    """升级到最新 Alembic 版本；唯一一份 0001 baseline 已构建完整 schema。"""
    cfg = Config(str(Path(__file__).parent / "alembic.ini"))
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
    # LISTEN 专线：ws_event_loop 内部直连 + 断线 5s 重连；显式注入 execute_cron_turn 消除模块导入序耦合。
    start_ws_event_loop(_raw_pg_dsn(), cron_turn_handler=execute_cron_turn)
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
            drain_cron_turns(),
            drain_user_sessions(),
            return_exceptions=True,
        )

        # IM 通道桥在 outbox 专线关闭前停稳：适配器任务可能还在写 WSEvent / 开 DB session。
        await stop_channel_manager()

        await stop_ws_event_loop()

        await ENGINE.dispose()
        await aclose()
        await aclose_all()


app = FastAPI(title=SETTINGS.app_name, lifespan=lifespan)
# CORS 通配：鉴权走 Bearer token（非 cookie），跨域请求不会带凭据，FastAPI 会拒绝 `*` + credentials 组合。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    allow_credentials=False,
)
app.state.limiter = limiter
app.middleware("http")(stash_user_id_middleware)
# 后注册 = Starlette 外层 wrapper：inbound 它先跑、response header 它最后写；覆盖所有 path（不只 /api/*），health/static 也带 ID。
app.middleware("http")(correlation_id_middleware)
# 兜底：ServerErrorMiddleware 在最外层，BaseHTTPMiddleware 抛 raise 时 user middleware 的 post-call_next 不跑；此处从 ContextVar 读 ID 写 header，让 500 / 404 路径也带 X-Request-ID。
app.add_exception_handler(Exception, correlated_exception_response)
app.add_exception_handler(RateLimitExceeded, rate_limit_exception_handler)
updates_dir = Path("updates").absolute()
updates_dir.mkdir(parents=True, exist_ok=True)
app.mount("/updates", StaticFiles(directory=str(updates_dir)), name="updates")


# 挂在根路径，避免外部 Docker HEALTHCHECK / k8s livenessProbe / uptime probe（默认请求 /health）返回 404。
@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


if SETTINGS.metrics_enabled:

    @app.get(SETTINGS.metrics_path)
    def metrics_endpoint(
        authorization: str | None = Header(default=None, alias="Authorization"),
        x_metrics_token: str | None = Header(default=None, alias="X-Metrics-Token"),
    ) -> Response:
        return render_metrics_response(auth_header=authorization, token_header=x_metrics_token)


for _router in ROUTERS:
    app.include_router(_router)
