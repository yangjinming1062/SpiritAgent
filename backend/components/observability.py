import contextvars
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

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

# 房间图生图成本与成功率：origin 区分 onboarding / outfit / user_request / llm / nightly；result 区分 ready / failed。
ROOM_BACKDROP_IMAGES_TOTAL = Counter(
    "spiritagent_room_backdrop_images_total",
    "Total room backdrop image generation attempts by origin and result",
    ["origin", "result"],
)
# LLM 主动触发房间图的当日成功计数（按用户本地日统计在应用层；这里只打原始事件）。
ROOM_BACKDROP_LLM_TRIGGERS_TOTAL = Counter(
    "spiritagent_room_backdrop_llm_triggers_total",
    "LLM-triggered room backdrop tool calls by outcome (accepted / rejected_locked / rejected_quota / rejected_work_preset / rejected_silent)",
    ["outcome"],
)
# 失败的人格化 utterance 标签（避免暴露 provider 名）
ROOM_BACKDROP_FAILURES_TOTAL = Counter(
    "spiritagent_room_backdrop_failures_total",
    "Room backdrop generation failures by stage (brief / imagine / store / image_error)",
    ["stage"],
)

_CURRENT_TRACE_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar("current_trace_id", default=None)
_CURRENT_SPAN_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar("current_span_id", default=None)


def _mint_trace_id() -> str:
    """mint 但不 bind：把 set+reset 留给 span 上下文管理器，避免在 stray 调用上泄露到后续请求。"""
    return secrets.token_hex(16)


@asynccontextmanager
async def async_trace_span(name: str, attributes: dict[str, Any] | None = None) -> AsyncIterator[dict[str, Any]]:
    """async 上下文管理器：记录 trace span 并上报 JSON-RPC 指标。"""
    trace_token: contextvars.Token | None = None
    trace_id = _CURRENT_TRACE_ID.get()
    if not trace_id:
        trace_id = _mint_trace_id()
        trace_token = _CURRENT_TRACE_ID.set(trace_id)
    span_id = secrets.token_hex(8)
    token_span = _CURRENT_SPAN_ID.set(span_id)
    start_time = time.monotonic()
    span_context = {"name": name, "trace_id": trace_id, "span_id": span_id, "attributes": attributes or {}}
    status = "ok"
    try:
        yield span_context
    except Exception:
        status = "error"
        raise
    finally:
        duration = time.monotonic() - start_time
        _CURRENT_SPAN_ID.reset(token_span)
        if trace_token is not None:
            _CURRENT_TRACE_ID.reset(trace_token)
        if name.startswith("rpc."):
            method = name.removeprefix("rpc.")
            RPC_REQUESTS_TOTAL.labels(method=method, status=status).inc()
            RPC_REQUEST_DURATION_SECONDS.labels(method=method).observe(duration)


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
    if not secrets.compare_digest(provided, expected_token):
        raise HTTPException(status_code=403, detail="Forbidden: Invalid metrics token")


def render_metrics_response(auth_header: str | None = None, token_header: str | None = None) -> Response:
    """渲染 Prometheus 指标响应（可选鉴权）。"""
    check_metrics_auth(auth_header, token_header)
    content = generate_latest()
    return Response(content=content, media_type=CONTENT_TYPE_LATEST)
