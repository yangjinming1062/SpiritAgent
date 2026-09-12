"""FastAPI 应用装配：中间件、异常处理、路由与静态目录；注册在导入期显式完成。"""

from pathlib import Path

from api import ROUTERS
from components import (
    SETTINGS,
    correlated_exception_response,
    correlation_id_middleware,
    render_metrics_response,
)
from fastapi import FastAPI, Header, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from services.adapters.http.rate_limit import (
    limiter,
    rate_limit_exception_handler,
    stash_user_id_middleware,
)
from slowapi.errors import RateLimitExceeded

from bootstrap.lifecycle import lifespan
from bootstrap.registrations import register_all

register_all()

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
