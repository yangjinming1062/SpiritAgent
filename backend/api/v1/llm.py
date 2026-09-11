from typing import Any

from common import get_router
from components import SESSION_LOCAL, SETTINGS, get_logger
from fastapi import HTTPException, Request
from modules.auth import CurrentUser
from modules.system import CompletionResponse
from pydantic import BaseModel
from services.llm import (
    MissingLlmConfigError,
    ServiceType,
    call_with_retry,
    classify_api_error,
    execute_with_fallback,
    resolve_context_tokens,
    resolve_provider_chain,
)
from services.rate_limit import limiter
from slowapi.util import get_remote_address

from ._http_errors import classified_http_exception, missing_config_http

logger = get_logger(__name__)

router = get_router()


class CompletionRequest(BaseModel):
    instructions: str = ""
    input: str | list[dict[str, Any]]
    model: str | None = None
    temperature: float | None = None
    max_output_tokens: int | None = None


@router.post("/completion", response_model=CompletionResponse)
@limiter.limit(lambda: f"{SETTINGS.llm_completion_rate_limit_per_minute}/minute")
@limiter.limit(lambda: f"{SETTINGS.llm_completion_rate_limit_per_ip_per_minute}/minute", key_func=get_remote_address)
async def create_completion(req: CompletionRequest, request: Request, user: CurrentUser) -> CompletionResponse:
    """Desktop Runner 代理 LLM 调用的无状态补全端点：错误响应走非泄露分类信封（异常细节留在服务端日志，renderer 只看到 {error, reason, status}，reason 为稳定 FailoverReason 枚举值，对应 docs/ARCHITECTURE.md §3.1 的 -32603 约束）；调用走 provider 链路，首个供应商遇鉴权/计费/模型不存在错误时自动透明切换下一家。"""

    async def _call(provider: Any) -> Any:
        client = provider.raw_client()
        if client is None:
            raise RuntimeError(f"provider {provider.provider_name} does not expose the Responses API")
        model = req.model or provider.config.model
        kwargs: dict[str, Any] = {"model": model, "instructions": req.instructions, "input": req.input, "store": False}
        if req.temperature is not None:
            kwargs["temperature"] = req.temperature
        if req.max_output_tokens is not None:
            kwargs["max_output_tokens"] = req.max_output_tokens
        return await call_with_retry(
            client,
            context_length=resolve_context_tokens(provider.provider_name, ServiceType.llm),
            **kwargs,
        )

    try:
        # 在会话内解析链路后立即释放连接，避免上游 await 期间长时间占用连接池；resolve_provider_chain 只读 SETTINGS + 单行 UserModelConfig，期间不再访问 DB。
        async with SESSION_LOCAL() as db:
            chain = await resolve_provider_chain(db, user.id, "llm")
        if not chain:
            raise missing_config_http("LLM")
        response = await execute_with_fallback(
            db=None,
            user_id=user.id,
            service_type="llm",
            call_fn=_call,
            _chain=chain,
        )
    except MissingLlmConfigError:
        raise missing_config_http("LLM")
    except HTTPException:
        raise
    except Exception as e:
        classified = classify_api_error(e, model=req.model or "")
        logger.warning(
            "LLM completion failed user=%s reason=%s status=%s",
            user.id,
            classified.reason.value,
            classified.status_code,
        )
        raise classified_http_exception(classified) from e

    content = response.output_text
    if not content:
        logger.warning("LLM returned 2xx with empty output user=%s", user.id)
        raise classified_http_exception(
            classify_api_error(RuntimeError("LLM returned no output"), model=req.model or ""),
        )
    usage = response.usage.model_dump() if response.usage else None

    return CompletionResponse(content=content, usage=usage)
