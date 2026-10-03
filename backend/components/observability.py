import secrets
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from .config import SETTINGS

RPC_REQUESTS_TOTAL = Counter(
    "spiritagent_rpc_requests_total",
    "Total JSON-RPC requests handled over WebSocket",
    ["method", "status"],
)
RPC_REQUEST_DURATION_SECONDS = Histogram(
    "spiritagent_rpc_request_duration_seconds",
    "JSON-RPC execution duration in seconds",
    ["method"],
)

# 场景图片提交与就绪结果；描述失败单独按阶段计数。
SCENE_IMAGES_TOTAL = Counter(
    "spiritagent_scene_images_total",
    "Total scene backdrop image generation attempts by origin and result",
    ["origin", "result"],
)
# 场景新增工具请求；付费额度由持久化提交账本管理。
SCENE_LLM_TRIGGERS_TOTAL = Counter(
    "spiritagent_scene_llm_triggers_total",
    "Scene creation tool requests by outcome (accepted / rejected)",
    ["outcome"],
)
# 场景失败阶段，不包含供应商或用户内容。
SCENE_FAILURES_TOTAL = Counter(
    "spiritagent_scene_failures_total",
    "Scene failures by stage (prepare / submitting / store / analyze)",
    ["stage"],
)


@asynccontextmanager
async def rpc_metrics(method: str) -> AsyncIterator[None]:
    """记录一次 JSON-RPC 方法执行的次数（按成功/失败）与耗时。"""
    start_time = time.monotonic()
    status = "ok"
    try:
        yield
    except Exception:
        status = "error"
        raise
    finally:
        RPC_REQUESTS_TOTAL.labels(method=method, status=status).inc()
        RPC_REQUEST_DURATION_SECONDS.labels(method=method).observe(time.monotonic() - start_time)


def check_metrics_auth(auth_header: str | None, token_header: str | None) -> None:
    """配置了 metrics_auth_token 时强制 token 鉴权。"""
    expected_token = SETTINGS.metrics_auth_token.strip()
    if not expected_token:
        return

    bearer_token = ""
    if auth_header and auth_header.lower().startswith("bearer "):
        bearer_token = auth_header[7:].strip()

    custom_token = (token_header or "").strip()
    provided = bearer_token or custom_token

    if not provided:
        raise HTTPException(status_code=401, detail="Metrics authentication required")
    if not secrets.compare_digest(provided.encode("utf-8"), expected_token.encode("utf-8")):
        raise HTTPException(status_code=403, detail="Forbidden: Invalid metrics token")


def render_metrics_response(auth_header: str | None = None, token_header: str | None = None) -> Response:
    """渲染 Prometheus 指标响应（可选鉴权）。"""
    check_metrics_auth(auth_header, token_header)
    content = generate_latest()
    return Response(content=content, media_type=CONTENT_TYPE_LATEST)
