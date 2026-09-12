import enum
import re
import ssl
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
import openai
from components import get_logger, redact_sensitive_text, safe_json_loads

from .providers.base import ProviderError, ProviderResultUnknownError

logger = get_logger(__name__)

_CAUSE_CHAIN_MAX_DEPTH = 5


class FailoverReason(enum.Enum):
    """API 调用失败原因枚举 —— 用于决定恢复策略。"""

    # 鉴权 / 授权
    auth = "auth"  # 401/403 瞬时鉴权失败 —— 刷新或轮换密钥

    # 计费 / 配额
    billing = "billing"  # 402 或确认的额度耗尽 —— 立即轮换密钥
    rate_limit = "rate_limit"  # 429 或配额限流 —— 退避后轮换

    # 服务端
    overloaded = "overloaded"  # 503/529 —— 供应商过载，退避重试
    server_error = "server_error"  # 500/502 —— 内部错误，可重试

    # 传输
    timeout = "timeout"  # 连接/读取超时 —— 重建客户端后重试
    result_unknown = "result_unknown"  # 非幂等请求可能已生效 —— 禁止自动重试或回退

    # 上下文 / 负载
    context_overflow = "context_overflow"  # 上下文超限 —— 压缩而非切换供应商
    payload_too_large = "payload_too_large"  # 413 —— 压缩请求负载
    image_too_large = "image_too_large"  # 单图超出供应商限制 —— 缩小后重试

    # 模型 / 供应商策略
    model_not_found = "model_not_found"  # 404 或模型无效 —— 回退到其他模型
    provider_policy_blocked = "provider_policy_blocked"  # 聚合商（如 OpenRouter）因账号隐私策略屏蔽唯一端点
    content_policy_blocked = "content_policy_blocked"  # 供应商安全过滤拒绝该 prompt —— 对同一请求是确定性的，不重试

    # 请求格式
    format_error = "format_error"  # 400 bad request —— 中止或剥离后重试
    invalid_encrypted_content = "invalid_encrypted_content"  # Responses 加密重放 blob 被拒 —— 剥离重放状态后重试
    attachment_fetch_failed = (
        "attachment_fetch_failed"  # 供应商拉取 image_url 内的 URL 失败；后端无法重试，需要返回给用户提示信息
    )

    # 供应商专属
    long_context_tier = "long_context_tier"  # Anthropic "extra usage" 长上下文档位门禁
    oauth_long_context_beta_forbidden = (
        "oauth_long_context_beta_forbidden"  # Anthropic OAuth 订阅拒绝 1M 上下文 beta —— 去掉 beta 后重试
    )

    # 兜底
    unknown = "unknown"  # 不可分类 —— 带退避重试


@dataclass
class ClassifiedError:
    """结构化的 API 错误分类与恢复建议。"""

    reason: FailoverReason
    status_code: int | None = None
    provider: str | None = None
    model: str | None = None
    message: str = ""
    error_context: dict[str, Any] = field(default_factory=dict)

    # 恢复动作提示 —— 重试循环直接读这些字段，不必自己再次分类。
    retryable: bool = True
    should_compress: bool = False
    should_rotate_credential: bool = False
    should_fallback: bool = False
    suggested_delay: float | None = None


_ClassifierBuilder = Callable[..., ClassifiedError]


# 403/404 消歧与无 SDK 信号的消息 fallback 用；主路径已被 SDK RateLimitError + _BILLING_ERROR_CODES 覆盖。
_BILLING_PATTERNS = (
    "insufficient credits",
    "credit balance",
    "credits exhausted",
    "exceeded your current quota",
    "plan does not include",
    "key limit exceeded",
)

# 仅在 Phase D（无 SDK 类、无结构化错误码）的兜底中使用；SDK RateLimitError + _RATE_LIMIT_ERROR_CODES 覆盖主路径。
_RATE_LIMIT_MESSAGE_PATTERNS = (
    "rate limit",
    "rate_limit",
    "too many requests",
    "rate limit exceeded",
    "try again in",
    "please retry after",
)

# 供应商 / 聚合商无法拉取 image_url 中的图像 URL：必须早于 format_error 匹配，以免用户看到被 400 文案误导。
_ATTACHMENT_FETCH_PATTERNS = (
    "unable to fetch image from url",
    "unable to fetch the image",
    "could not fetch image",
    "error fetching image",
    "failed to download image from url",
)

# 用量上限 —— 402 与部分 429/400 消息中"quota"等词需进一步消歧（瞬时 vs 计费耗尽）。
_USAGE_LIMIT_PATTERNS = ("usage limit", "quota", "limit exceeded", "key limit exceeded")
_USAGE_LIMIT_TRANSIENT_SIGNALS = ("try again", "resets at", "reset in", "requests remaining", "periodic")

# 从消息文本（无 status_code 字段）识别 payload 过大：代理或后端把 HTTP 状态码嵌入错误信息。
_PAYLOAD_TOO_LARGE_PATTERNS = ("request entity too large", "payload too large", "error code: 413")

# 单图过大：在 400 而非 413 上匹配，因为多数供应商在整请求超 413 前会先以
# 400 + 明确图像过大提示返回；Anthropic 单图 5 MB 上限为关键场景。
_IMAGE_TOO_LARGE_PATTERNS = (
    "image exceeds",  # Anthropic: "image exceeds 5 MB maximum"
    "image too large",  # 通用
    "image_too_large",  # error_code 变体
    "image size exceeds",  # 变体
    "image dimensions exceed",  # Anthropic: "image dimensions exceed max allowed size: 8000 pixels"
    "max allowed size: 8000",  # Anthropic 尺寸上限（像素数显式）
)

# 生图供应商返回 200 但零张图：同一供应商上重试无意义，下一家可能成功。
_EMPTY_IMAGE_RESULT_PATTERNS = ("returned no images",)


# 模型存在但拒绝图像/视频输入 —— 走 model_not_found 的回退路径。
_VISION_UNSUPPORTED_PATTERNS = (
    "no endpoints found that support image input",  # mimo token-plan verbatim
    "does not support image input",
    "image input not supported",
    "does not support vision",
    "multimodal input not supported",
    "does not support multimodal",
    "input_video",  # Responses 网关拒收视频项（mimo verbatim: "input item type 'input_video' is not supported"）
    "video input not supported",
    "does not support video",
)

# 上下文溢出模式（BadRequestError 内 sub-bucket —— SDK 不区分 sub-class，跨供应商只能靠消息消歧）
_CONTEXT_OVERFLOW_PATTERNS = (
    "context length",
    "context size",
    "maximum context",
    "token limit",
    "too many tokens",
    "reduce the length",
    "exceeds the limit",
    "context window",
    "prompt is too long",
    "prompt exceeds max length",
    "maximum number of tokens",
    # vLLM / 本地推理服务
    "exceeds the max_model_len",
    "max_model_len",
    "prompt length",  # "engine prompt length X exceeds"
    "input is too long",
    "maximum model length",
    # Ollama
    "context length exceeded",
    "truncating input",
    # llama.cpp / llama-server
    "slot context",  # "slot context: N tokens, prompt N tokens"
    "n_ctx_slot",
    # 中文错误信息
    "超过最大长度",
    "上下文长度",
    # AWS Bedrock Converse API
    "max input token",
    "exceeds the maximum number of input tokens",
)

# 模型未找到模式（_MODEL_NOT_FOUND_ERROR_CODES 已覆盖代码层 case；
# 此处仅作 message fallback）
_MODEL_NOT_FOUND_PATTERNS = ("is not a valid model", "invalid model", "model not found", "model_not_found")

# 请求校验失败模式：请求畸形，每次重试结果相同；某些 OpenAI 兼容网关（如 codex.nekos.me）
# 会把它当作 5xx 返回，会让通用 "5xx → 可重试" 规则误触发重试风暴。
# 命中后归为不可重试的 format_error，快速失败并回退。
_REQUEST_VALIDATION_PATTERNS = (
    "unknown parameter",
    "unsupported parameter",
    "unrecognized request argument",
    "invalid_request_error",
    "unknown_parameter",
    "unsupported_parameter",
)

_REQUEST_VALIDATION_ERROR_CODES = frozenset({"invalid_request_error", "unknown_parameter", "unsupported_parameter"})

# 当用户 OpenRouter 账号隐私设置（或单次请求的 `provider.data_collection: deny` 偏好）
# 排除唯一可用端点时，OpenRouter 返回带特定文案的 404，区别于 "model not found"：
# 模型实际存在，回退到其他供应商也无效（账号级设置适用于同一账号所有请求），
# 错误体已包含修复 URL。
_PROVIDER_POLICY_BLOCKED_PATTERNS = (
    "no endpoints available matching your guardrail",
    "no endpoints available matching your data policy",
    "no endpoints found matching your data policy",
)

# 供应商内容策略 / 安全过滤拦截：与上文的 OpenRouter 账号级策略（``provider_policy_blocked``）不同，
# 这是供应商对单条 prompt 的安全判断，对同一请求是确定性的，三次重试只会重复同一拒绝。
# 命中后立即切换到配置的兜底模型 / 供应商；无兜底则返回给用户带修复建议的消息。
# 模式故意收窄，避免与计费 / 鉴权 / 格式错误误撞。
_CONTENT_POLICY_BLOCKED_PATTERNS = (
    # OpenAI Codex —— 消息可能不带 HTTP 状态码
    "flagged for possible cybersecurity risk",
    "trusted access for cyber",
    # OpenAI 内容审核 —— chat completions / responses
    "violates our usage policies",
    "violates openai's usage policies",
    "your request was flagged by",
    # Anthropic 安全系统
    "prompt was flagged by our safety",
    "responses cannot be generated due to safety",
    # MiniMax（base_resp.status_code 1027）安全拒绝原文
    "violated safety policy",
    # MiniMax（base_resp.status_code 1026）敏感输入审核，``new_sensitive`` 为该码的原文 status_msg
    "new_sensitive",
    # Azure / OpenAI Responses 通用文案：``content_filter``（下划线）是 OpenAI 标准 token，
    # ``responsibleaipolicyviolation`` 是 Azure 错误码；不匹配带空格的 "content filter" —
    # 后者出现在良性配置描述中
    "content_filter",
    "responsibleaipolicyviolation",
    # Gemini 生图 IMAGE_SAFETY + finishMessage 含 "Generative AI Prohibited Use policy"
    "image_safety",
    "generative ai prohibited use policy",
)


def is_content_policy_error_message(msg: str) -> bool:
    """用与 ``classify_api_error`` 相同的模式列表对原始字符串做匹配。"""
    return any(p in msg.lower() for p in _CONTENT_POLICY_BLOCKED_PATTERNS)


# 供应商侧超时（即使异常类型是通用类型，如本地 shim 包装子进程超时产生的 RuntimeError）
# 的消息字符串模式；在基于异常类型的传输启发式之前检查，
# 避免自定义供应商的 "timed out" 落入 unknown 而被错报为空响应。
_TIMEOUT_MESSAGE_PATTERNS = ("timed out", "deadline exceeded", "request timed out")

# 服务端断开模式（无状态码但属于传输层）：大会话 + 这些模式触发带压缩的上下文溢出恢复路径。
_SERVER_DISCONNECT_PATTERNS = (
    "server disconnected",
    "peer closed connection",
    "connection reset by peer",
    "connection was closed",
    "network connection lost",
    "unexpected eof",
)

# SSL/TLS 瞬时失败模式：ssl.SSLError isinstance 覆盖大多数情况；
# 这里仅作为 message-text 兜底，匹配稳定子串以兼容 OpenSSL 格式变更。
_SSL_TRANSIENT_PATTERNS = (
    "bad record mac",  # 人类可读（Python ssl，多数 SDK）
    "ssl handshake failure",
    "tlsv1 alert",
    "[ssl:",  # Python ssl 模块前缀，如 "[SSL: BAD_RECORD_MAC]"
)

_BILLING_ERROR_CODES = frozenset(
    {
        "insufficient_quota",
        "billing_not_active",
        "payment_required",
        "insufficient_credits",
        "no_usable_credits",
        "balance_depleted",
        "model_not_supported_on_free_tier",
    },
)

_RATE_LIMIT_ERROR_CODES = frozenset({"resource_exhausted", "throttled", "rate_limit_exceeded"})

_MODEL_NOT_FOUND_ERROR_CODES = frozenset({"model_not_found", "model_not_available", "invalid_model"})

_CONTEXT_OVERFLOW_ERROR_CODES = frozenset({"context_length_exceeded", "max_tokens_exceeded"})


def classify_api_error(
    error: Exception,
    *,
    provider: str = "",
    model: str = "",
    approx_tokens: int = 0,
    context_length: int = 200000,
    num_messages: int = 0,
) -> ClassifiedError:
    """按优先级把 API 异常分类为结构化的恢复建议（供应商特定模式 → SDK 异常类层级 →
    结构化错误码 → 状态码 → 消息模式 → 大会话断连 → SSL 告警 → unknown 兜底）。
    """
    status_code = _extract_status_code(error)
    suggested_delay = _extract_suggested_delay(error)
    body = _extract_error_body(error)
    error_code = _extract_error_code(error, body)

    # 拼出尽可能完整的错误消息字符串供模式匹配：str(error) 不一定包含 body（如 OpenAI SDK APIStatusError
    # .__str__ 只返回首参），需追加 body 中的 message；并提取 metadata.raw ——
    # OpenRouter 把上游供应商错误包成 {"error":{"message":"Provider returned error",
    # "metadata":{"raw":"<实际 JSON>"}}}，真实消息（如 "context length exceeded"）只在最内层 JSON。
    error_msg = _build_error_message(error, body)
    provider_lower = (provider or "").strip().lower()
    model_lower = (model or "").strip().lower()

    def _result(reason: FailoverReason, **overrides) -> ClassifiedError:
        defaults = {
            "reason": reason,
            "status_code": status_code,
            "provider": provider,
            "model": model,
            "message": _extract_message(error, body),
            "suggested_delay": suggested_delay,
        }
        defaults.update(overrides)
        return ClassifiedError(**defaults)

    # 优先匹配：供应商特定模式（早于 SDK 类与状态码 —— 内容策略必须早于 400 format_error）
    if special := _classify_provider_specific(error_msg, status_code, _result):
        return special

    # Phase A — SDK 异常类层级 dispatch（绝大多数带 SDK 异常的请求在此终结）
    if classified := _classify_by_exception_type(
        error,
        status_code,
        body,
        error_code,
        error_msg,
        provider=provider_lower,
        model=model_lower,
        approx_tokens=approx_tokens,
        context_length=context_length,
        num_messages=num_messages,
        result_fn=_result,
    ):
        return classified

    # Phase B — 结构化错误码（按 error.code / body["error"]["code"] / body["code"] 顺序读）
    if error_code:
        classified = _classify_by_error_code(error_code, error_msg, _result)
        if classified is not None:
            return classified

    # Phase C — 状态码兜底（剩余场景：ProviderError 等无 SDK 类的边界类型）
    if status_code is not None:
        classified = _classify_by_status(
            status_code,
            error_msg,
            error_code,
            body,
            provider=provider_lower,
            model=model_lower,
            approx_tokens=approx_tokens,
            context_length=context_length,
            num_messages=num_messages,
            result_fn=_result,
        )
        if classified is not None:
            return classified

    # Phase D — 消息模式 fallback（无 SDK 类、无状态码、无错误码的纯裸消息）
    classified = _classify_by_message(
        error_msg,
        approx_tokens=approx_tokens,
        context_length=context_length,
        result_fn=_result,
    )
    if classified is not None:
        return classified

    # 服务端断开 + 大会话 → 上下文溢出（必须早于通用传输启发式：
    # 否则 RemoteProtocolError 总会映射到 timeout，不分会话大小）
    if any(p in error_msg for p in _SERVER_DISCONNECT_PATTERNS):
        # 绝对 token / 消息数阈值只是小上下文窗口的近似；
        # 大上下文会话可能在远低于实际 token 预算时就累积上百条消息。
        is_large = approx_tokens > context_length * 0.6 or (
            context_length <= 256000 and (approx_tokens > 120000 or num_messages > 200)
        )
        if is_large:
            return _result(FailoverReason.context_overflow, retryable=True, should_compress=True)
        return _result(FailoverReason.timeout, retryable=True)

    # SSL/TLS 瞬时 alert 走超时路径而非压缩 —— 是传输层抖动，不是上下文溢出；
    # 优先匹配以避免大会话上的 TLS 抖动触发不必要的上下文压缩。
    if any(p in error_msg for p in _SSL_TRANSIENT_PATTERNS):
        return _result(FailoverReason.timeout, retryable=True)

    return _result(FailoverReason.unknown, retryable=True)


def _classify_provider_specific(
    error_msg: str,
    status_code: int | None,
    result_fn: _ClassifierBuilder,
) -> ClassifiedError | None:
    """优先于 SDK 类 / 状态码的供应商特定模式（必须早于 SDK dispatch，
    否则内容安全过滤会被降级为通用 400）。
    """
    # 内容策略 / 安全过滤拦截（必须早于状态码分类：避免 400 安全拦截被降级为通用 format_error，
    # 避免无状态码的拦截（OpenAI Codex SDK 可不带）落入可重试的 unknown）
    if any(p in error_msg for p in _CONTENT_POLICY_BLOCKED_PATTERNS):
        return result_fn(FailoverReason.content_policy_blocked, retryable=False, should_fallback=True)

    # Anthropic 长上下文档位门禁（429 "extra usage" + "long context"）
    if status_code == 429 and "extra usage" in error_msg and "long context" in error_msg:
        return result_fn(FailoverReason.long_context_tier, retryable=True, should_compress=True)

    # Anthropic OAuth 订阅拒绝 1M 上下文 beta header：原文
    # "The long context beta is not yet available for this subscription.";
    # 状态码与文案均与上一条 429 门禁不同，不会冲突。
    if status_code == 400 and "long context beta" in error_msg and "not yet available" in error_msg:
        return result_fn(FailoverReason.oauth_long_context_beta_forbidden, retryable=True, should_compress=False)

    # xAI Grok 订阅 entitlement 错误（403 由 _classify_by_status 走 auth；
    # SSE ``type=error`` 不带 status_code 时落 unknown 烧光重试 —— 这里拦截）
    if "do not have an active grok subscription" in error_msg or (
        "out of available resources" in error_msg and "grok" in error_msg
    ):
        return result_fn(FailoverReason.auth, retryable=False, should_fallback=True)

    return None


def _classify_by_exception_type(
    error: Exception,
    status_code: int | None,
    body: dict,
    error_code: str,
    error_msg: str,
    *,
    provider: str,
    model: str,
    approx_tokens: int,
    context_length: int,
    num_messages: int,
    result_fn: _ClassifierBuilder,
) -> ClassifiedError | None:
    """按 SDK 异常类层级 dispatch（Phase A）—— openai / httpx / stdlib / ProviderError，most-specific-first。"""
    if isinstance(error, ProviderResultUnknownError):
        return result_fn(FailoverReason.result_unknown, retryable=False, should_fallback=False)

    # OpenAI SDK 状态码异常族（4xx/5xx —— 含 status_code）
    if isinstance(error, openai.RateLimitError):
        return result_fn(FailoverReason.rate_limit, retryable=True, should_rotate_credential=True, should_fallback=True)
    if isinstance(error, openai.AuthenticationError):
        return result_fn(FailoverReason.auth, retryable=False, should_rotate_credential=True, should_fallback=True)
    if isinstance(error, openai.PermissionDeniedError):
        return _classify_403(error_msg, result_fn)
    if isinstance(error, openai.NotFoundError):
        return _classify_404(error_msg, result_fn)
    if isinstance(error, openai.BadRequestError):
        return _classify_400(
            error_msg,
            error_code,
            body,
            provider=provider,
            model=model,
            approx_tokens=approx_tokens,
            context_length=context_length,
            num_messages=num_messages,
            result_fn=result_fn,
        )
    if isinstance(error, openai.UnprocessableEntityError | openai.ConflictError):
        return result_fn(FailoverReason.format_error, retryable=False, should_fallback=True)
    # APIResponseValidationError（APIError 子类，非 APIStatusError）：有 .response 但无 .status_code；
    # status_code 已由 _extract_status_code 从 .response.status_code 修复。
    api_response_validation_error = getattr(openai, "APIResponseValidationError", None)
    if api_response_validation_error is not None and isinstance(error, api_response_validation_error):
        if status_code is not None and any(p in error_msg for p in _CONTEXT_OVERFLOW_PATTERNS):
            return result_fn(FailoverReason.context_overflow, retryable=True, should_compress=True)
        return result_fn(FailoverReason.format_error, retryable=False, should_fallback=True)
    if isinstance(error, openai.APIStatusError):
        if status_code is not None:
            return _classify_by_status(
                status_code,
                error_msg,
                error_code,
                body,
                provider=provider,
                model=model,
                approx_tokens=approx_tokens,
                context_length=context_length,
                num_messages=num_messages,
                result_fn=result_fn,
            )
        return result_fn(FailoverReason.format_error, retryable=False, should_fallback=True)

    # OpenAI SDK 传输层异常（无 status_code；APITimeoutError 是 APIConnectionError 的子类）
    if isinstance(error, openai.APIConnectionError):
        return result_fn(FailoverReason.timeout, retryable=True)

    # httpx 异常族 —— HTTPStatusError 的状态码在 .response.status_code（已由 _extract_status_code 提取）
    if isinstance(error, httpx.HTTPStatusError):
        if status_code is not None:
            return _classify_by_status(
                status_code,
                error_msg,
                error_code,
                body,
                provider=provider,
                model=model,
                approx_tokens=approx_tokens,
                context_length=context_length,
                num_messages=num_messages,
                result_fn=result_fn,
            )
        return result_fn(FailoverReason.format_error, retryable=False, should_fallback=True)
    if isinstance(error, httpx.TimeoutException):
        return result_fn(FailoverReason.timeout, retryable=True)
    if isinstance(error, httpx.RequestError):
        return result_fn(FailoverReason.timeout, retryable=True)

    # 标准库传输 / SSL
    if isinstance(error, TimeoutError | ConnectionError | OSError | ssl.SSLError):
        return result_fn(FailoverReason.timeout, retryable=True)

    # 内部边界类型（Gemini/Zhipu/raw httpx 走 ProviderError 而非 SDK；状态码缺位时 defer 到 Phase B/D）
    if isinstance(error, ProviderError):
        if status_code is not None:
            return _classify_by_status(
                status_code,
                error_msg,
                error_code,
                body,
                provider=provider,
                model=model,
                approx_tokens=approx_tokens,
                context_length=context_length,
                num_messages=num_messages,
                result_fn=result_fn,
            )
        return None

    return None


def _classify_by_status(
    status_code: int,
    error_msg: str,
    error_code: str,
    body: dict,
    *,
    provider: str,
    model: str,
    approx_tokens: int,
    context_length: int,
    num_messages: int = 0,
    result_fn: _ClassifierBuilder,
) -> ClassifiedError | None:
    """基于 HTTP 状态码分类，并结合消息文案做精化（Phase C，主要承接 ProviderError 等无 SDK 类的场景）。"""
    match status_code:
        case 401:
            return result_fn(FailoverReason.auth, retryable=False, should_rotate_credential=True, should_fallback=True)

        case 403:
            return _classify_403(error_msg, result_fn)

        case 402:
            return _classify_402(error_msg, result_fn)

        case 404:
            return _classify_404(error_msg, result_fn)

        case 413:
            return result_fn(FailoverReason.payload_too_large, retryable=True, should_compress=True)

        case 429:
            return result_fn(
                FailoverReason.rate_limit,
                retryable=True,
                should_rotate_credential=True,
                should_fallback=True,
            )

        case 400:
            return _classify_400(
                error_msg,
                error_code,
                body,
                provider=provider,
                model=model,
                approx_tokens=approx_tokens,
                context_length=context_length,
                num_messages=num_messages,
                result_fn=result_fn,
            )

        case 500 | 502:
            # 某些 OpenAI 兼容网关（codex.nekos.me 对 unknown/unsupported parameter 返回 502）
            # 把请求校验错误以 5xx 返回 —— 是确定性的，命中后归为不可重试的 format_error，
            # 避免重试风暴。
            if (
                any(p in error_msg for p in _REQUEST_VALIDATION_PATTERNS)
                or error_code.lower() in _REQUEST_VALIDATION_ERROR_CODES
            ):
                return result_fn(FailoverReason.format_error, retryable=False, should_fallback=True)
            return result_fn(FailoverReason.server_error, retryable=True)

        case 503 | 529:
            return result_fn(FailoverReason.overloaded, retryable=True)

        case s if 400 <= s < 500:
            return result_fn(FailoverReason.format_error, retryable=False, should_fallback=True)

        case s if 500 <= s < 600:
            return result_fn(FailoverReason.server_error, retryable=True)

        case _:
            return None


def _classify_403(error_msg: str, result_fn: _ClassifierBuilder) -> ClassifiedError:
    """403 → billing vs auth 消歧（PermissionDeniedError SDK 类层级无法区分）。"""
    if (
        "key limit exceeded" in error_msg
        or "spending limit" in error_msg
        or any(p in error_msg for p in _BILLING_PATTERNS)
    ):
        return result_fn(FailoverReason.billing, retryable=False, should_rotate_credential=True, should_fallback=True)
    return result_fn(FailoverReason.auth, retryable=False, should_fallback=True)


def _classify_404(error_msg: str, result_fn: _ClassifierBuilder) -> ClassifiedError:
    """对 404 进行分类（计费 / 策略屏蔽 / 模型缺失 / 未知）。"""
    # Nous API 把 Free Tier 付费模型失效以 404 而非 402 返回 ——
    # 视作 entitlement / 计费耗尽而非模型缺失，便于展示充值指引。
    if any(p in error_msg for p in _BILLING_PATTERNS):
        return result_fn(FailoverReason.billing, retryable=False, should_rotate_credential=True, should_fallback=True)
    # OpenRouter 策略屏蔽 404：模型实际存在，回退也无效（账号级设置对同账号所有调用生效），错误体已含修复 URL。
    if any(p in error_msg for p in _PROVIDER_POLICY_BLOCKED_PATTERNS):
        return result_fn(FailoverReason.provider_policy_blocked, retryable=False, should_fallback=False)
    if any(p in error_msg for p in _MODEL_NOT_FOUND_PATTERNS):
        return result_fn(FailoverReason.model_not_found, retryable=False, should_fallback=True)
    # 不支持视觉：回退到具备视觉能力的供应商。
    if any(p in error_msg for p in _VISION_UNSUPPORTED_PATTERNS):
        return result_fn(FailoverReason.model_not_found, retryable=False, should_fallback=True)
    # 通用 404 无模型缺失信号：可能是端点路径错配（本地 llama.cpp / Ollama / vLLM）、
    # 代理路由抖动或后端瞬时问题；归为 unknown 让重试循环把真实错误暴露给上层。
    return result_fn(FailoverReason.unknown, retryable=True)


def _classify_402(error_msg: str, result_fn: _ClassifierBuilder) -> ClassifiedError:
    """消歧 402：计费耗尽 vs 瞬时用量上限。"Usage limit, try again in 5 minutes" 之类的
    周期配额属限流而非计费。
    """
    has_usage_limit = any(p in error_msg for p in _USAGE_LIMIT_PATTERNS)
    has_transient_signal = any(p in error_msg for p in _USAGE_LIMIT_TRANSIENT_SIGNALS)

    if has_usage_limit and has_transient_signal:
        return result_fn(FailoverReason.rate_limit, retryable=True, should_rotate_credential=True, should_fallback=True)

    return result_fn(FailoverReason.billing, retryable=False, should_rotate_credential=True, should_fallback=True)


def _classify_400(
    error_msg: str,
    error_code: str,
    body: dict,
    *,
    provider: str,
    model: str,
    approx_tokens: int,
    context_length: int,
    num_messages: int = 0,
    result_fn: _ClassifierBuilder,
) -> ClassifiedError:
    """对 400 Bad Request 进行分类（上下文溢出 / 格式错误 / 通用）。"""
    # 不支持视觉（早于 image_too_large：恢复路径不同）
    if any(p in error_msg for p in _VISION_UNSUPPORTED_PATTERNS):
        return result_fn(FailoverReason.model_not_found, retryable=False, should_fallback=True)

    # 400 上的 image_too_large（Anthropic 5 MB 单图检查即此方式返回；
    # 早于 context_overflow：消息可能同时命中两模式 ("exceeds"+"image")，缩图是更便宜的恢复路径）
    if any(p in error_msg for p in _IMAGE_TOO_LARGE_PATTERNS):
        return result_fn(FailoverReason.image_too_large, retryable=True)

    # OpenAI Responses 的加密推理重放 blob 失效（早于 context_overflow：部分文案含上下文类措辞）
    error_code_lower = (error_code or "").lower()
    if (
        error_code_lower == "invalid_encrypted_content"
        or "invalid_encrypted_content" in error_msg
        or ("encrypted content for item" in error_msg and "could not be verified" in error_msg)
    ):
        return result_fn(FailoverReason.invalid_encrypted_content, retryable=True, should_fallback=False)

    # 错误码 ``context_length_exceeded`` / ``max_tokens_exceeded`` 是 OpenAI/Anthropic 在 400 上的标准信号，
    # SDK 类层级无法表达 —— 必须在此用 error_code 消歧
    # （避免错过结构化信号后再走 Phase B —— Phase A 先终结）。
    if error_code_lower in _CONTEXT_OVERFLOW_ERROR_CODES or any(p in error_msg for p in _CONTEXT_OVERFLOW_PATTERNS):
        return result_fn(FailoverReason.context_overflow, retryable=True, should_compress=True)

    # 部分供应商（OpenRouter）以 400 而非 404 返回 model-not-found
    if any(p in error_msg for p in _PROVIDER_POLICY_BLOCKED_PATTERNS):
        return result_fn(FailoverReason.provider_policy_blocked, retryable=False, should_fallback=False)
    if any(p in error_msg for p in _MODEL_NOT_FOUND_PATTERNS):
        return result_fn(FailoverReason.model_not_found, retryable=False, should_fallback=True)

    # 部分供应商以 400 而非 429/402 返回限流 / 计费错误
    if any(p in error_msg for p in _RATE_LIMIT_MESSAGE_PATTERNS):
        return result_fn(FailoverReason.rate_limit, retryable=True, should_rotate_credential=True, should_fallback=True)
    if any(p in error_msg for p in _BILLING_PATTERNS):
        return result_fn(FailoverReason.billing, retryable=False, should_rotate_credential=True, should_fallback=True)

    # 供应商无法拉取 image_url 内的 URL（区别于通用 format_error —— 重试也无效，需返回给用户）
    if any(p in error_msg for p in _ATTACHMENT_FETCH_PATTERNS):
        return result_fn(FailoverReason.attachment_fetch_failed, retryable=False, should_fallback=False)

    # 通用 400 + 大会话 → 可能为上下文溢出（Anthropic 上下文过大时偶尔返回裸 "Error" 消息）
    err_body_msg = _extract_body_message(body)
    is_generic = len(err_body_msg) < 30 or err_body_msg in {"error", ""}
    # 绝对 token / 消息数阈值只是小上下文窗口的近似；大上下文会话可能在远低于预算时就累积很多消息。
    is_large = approx_tokens > context_length * 0.4 or (
        context_length <= 256000 and (approx_tokens > 80000 or num_messages > 80)
    )

    if is_generic and is_large:
        return result_fn(FailoverReason.context_overflow, retryable=True, should_compress=True)

    return result_fn(FailoverReason.format_error, retryable=False, should_fallback=True)


def _classify_by_error_code(error_code: str, error_msg: str, result_fn: _ClassifierBuilder) -> ClassifiedError | None:
    """基于响应体里的结构化错误码分类。"""
    code_lower = error_code.lower()
    if code_lower in _RATE_LIMIT_ERROR_CODES:
        return result_fn(FailoverReason.rate_limit, retryable=True, should_rotate_credential=True)
    if code_lower in _BILLING_ERROR_CODES:
        return result_fn(FailoverReason.billing, retryable=False, should_rotate_credential=True, should_fallback=True)
    if code_lower in _MODEL_NOT_FOUND_ERROR_CODES:
        return result_fn(FailoverReason.model_not_found, retryable=False, should_fallback=True)
    if code_lower in _CONTEXT_OVERFLOW_ERROR_CODES:
        return result_fn(FailoverReason.context_overflow, retryable=True, should_compress=True)
    if code_lower == "invalid_encrypted_content":
        return result_fn(FailoverReason.invalid_encrypted_content, retryable=True, should_fallback=False)
    return None


def _classify_by_message(
    error_msg: str,
    *,
    approx_tokens: int,
    context_length: int,
    result_fn: _ClassifierBuilder,
) -> ClassifiedError | None:
    """在没有 SDK 类 / 状态码 / 错误码时基于错误消息模式分类（Phase D 兜底）。"""
    if any(p in error_msg for p in _PAYLOAD_TOO_LARGE_PATTERNS):
        return result_fn(FailoverReason.payload_too_large, retryable=True, should_compress=True)

    if any(p in error_msg for p in _IMAGE_TOO_LARGE_PATTERNS):
        return result_fn(FailoverReason.image_too_large, retryable=True)

    # 生图供应商返回成功但零张图：交给下一家
    if any(p in error_msg for p in _EMPTY_IMAGE_RESULT_PATTERNS):
        return result_fn(FailoverReason.unknown, retryable=False, should_fallback=True)

    # 用量上限需消歧：含瞬时信号（try again / resets at 等）说明是周期配额而非计费耗尽
    if any(p in error_msg for p in _USAGE_LIMIT_PATTERNS):
        has_transient_signal = any(p in error_msg for p in _USAGE_LIMIT_TRANSIENT_SIGNALS)
        if has_transient_signal:
            return result_fn(
                FailoverReason.rate_limit,
                retryable=True,
                should_rotate_credential=True,
                should_fallback=True,
            )
        return result_fn(FailoverReason.billing, retryable=False, should_rotate_credential=True, should_fallback=True)

    if any(p in error_msg for p in _BILLING_PATTERNS):
        return result_fn(FailoverReason.billing, retryable=False, should_rotate_credential=True, should_fallback=True)

    if any(p in error_msg for p in _RATE_LIMIT_MESSAGE_PATTERNS):
        return result_fn(FailoverReason.rate_limit, retryable=True, should_rotate_credential=True, should_fallback=True)

    if any(p in error_msg for p in _CONTEXT_OVERFLOW_PATTERNS):
        return result_fn(FailoverReason.context_overflow, retryable=True, should_compress=True)

    if any(p in error_msg for p in _PROVIDER_POLICY_BLOCKED_PATTERNS):
        return result_fn(FailoverReason.provider_policy_blocked, retryable=False, should_fallback=False)

    if any(p in error_msg for p in _MODEL_NOT_FOUND_PATTERNS):
        return result_fn(FailoverReason.model_not_found, retryable=False, should_fallback=True)

    # 本地 shim / 自定义供应商用通用异常（RuntimeError）包装子进程 / HTTP 超时 ——
    # 归为传输超时，让重试循环重建客户端而非视为空响应。
    if any(p in error_msg for p in _TIMEOUT_MESSAGE_PATTERNS):
        return result_fn(FailoverReason.timeout, retryable=True)

    return None


def _build_error_message(error: Exception, body: dict) -> str:
    """拼出尽可能完整的错误消息：str(error) + body message + OpenRouter 嵌套的 metadata.raw ——
    上游的真实错误常埋在最深一层。
    """
    raw_msg = str(error).lower()
    body_msg = ""
    metadata_msg = ""
    if isinstance(body, dict):
        err_obj = body.get("error", {})
        if isinstance(err_obj, dict):
            body_msg = str(err_obj.get("message") or "").lower()
            metadata = err_obj.get("metadata", {})
            if isinstance(metadata, dict):
                raw_json = metadata.get("raw") or ""
                if isinstance(raw_json, str) and raw_json.strip():
                    inner = safe_json_loads(raw_json)
                    if isinstance(inner, dict):
                        inner_err = inner.get("error", {})
                        if isinstance(inner_err, dict):
                            metadata_msg = str(inner_err.get("message") or "").lower()
        if not body_msg:
            body_msg = str(body.get("message") or "").lower()
    parts = [raw_msg]
    if body_msg and body_msg not in raw_msg:
        parts.append(body_msg)
    if metadata_msg and metadata_msg not in raw_msg and metadata_msg not in body_msg:
        parts.append(metadata_msg)
    return " ".join(parts)


def _extract_body_message(body: dict) -> str:
    if not isinstance(body, dict):
        return ""
    err_obj = body.get("error", {})
    if isinstance(err_obj, dict):
        msg = str(err_obj.get("message") or "").strip().lower()
        if msg:
            return msg
    return str(body.get("message") or "").strip().lower()


def _extract_status_code(error: Exception) -> int | None:
    """沿异常链向上查找 HTTP 状态码：``.status_code`` / ``.status`` / ``.response.status_code``
    （覆盖 httpx.HTTPStatusError / openai.APIResponseValidationError）。
    """
    current = error
    for _ in range(_CAUSE_CHAIN_MAX_DEPTH):
        code = getattr(current, "status_code", None)
        if isinstance(code, int):
            return code
        code = getattr(current, "status", None)  # 部分 SDK 用 .status 而非 .status_code
        if isinstance(code, int) and 100 <= code < 600:
            return code
        # httpx.HTTPStatusError / openai.APIResponseValidationError：状态码挂在 .response.status_code
        response = getattr(current, "response", None)
        if response is not None:
            code = getattr(response, "status_code", None)
            if isinstance(code, int) and 100 <= code < 600:
                return code
        cause = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
        if cause is None or cause is current:
            break
        current = cause
    return None


def _extract_error_body(error: Exception) -> dict:
    """从 SDK 异常中取出结构化的错误体（``.body`` 直接拿 dict；否则从 ``.response.json()`` 解析）。"""
    body = getattr(error, "body", None)
    if isinstance(body, dict):
        return body
    response = getattr(error, "response", None)
    if response is not None:
        try:
            json_body = response.json()
            if isinstance(json_body, dict):
                return json_body
        except Exception:
            logger.debug("failed to parse error response body as JSON", exc_info=True)
    return {}


def _extract_error_code(error: Exception, body: dict) -> str:
    """按优先级读 SDK 异常属性 + body 字段提取结构化错误码：
    e.code → e.type → body["error"]["code"] → body["error"]["type"] → body["code"] →
    body["error_code"] → Responses-API message 内嵌 JSON。
    """
    # 1. SDK 异常自身的 .code / .type 字段（APIError 基类提供；
    # openai-shaped 响应通常 None，因为 real shape 把 code 嵌套在 body["error"]["code"]）
    code = getattr(error, "code", None)
    if isinstance(code, str) and code.strip() and code.strip() != "400":
        return code.strip()
    type_attr = getattr(error, "type", None)
    if isinstance(type_attr, str) and type_attr.strip() and type_attr.strip() != "400":
        return type_attr.strip()

    if not body:
        return ""

    def _code_from_payload(payload) -> str:
        if not isinstance(payload, dict):
            return ""
        payload_error = payload.get("error", {})
        if isinstance(payload_error, dict):
            nested = payload_error.get("code") or payload_error.get("type") or ""
            if isinstance(nested, str) and nested.strip() and nested.strip() != "400":
                return nested.strip()
        code = payload.get("code") or payload.get("error_code") or ""
        if isinstance(code, str | int):
            text = str(code).strip()
            if text and text != "400":
                return text
        return ""

    error_obj = body.get("error", {})
    if isinstance(error_obj, dict):
        # 2. body["error"]["code"] / body["error"]["type"]（OpenAI real shape 标准位置）
        code = error_obj.get("code") or error_obj.get("type") or ""
        if isinstance(code, str) and code.strip() and code.strip() != "400":
            return code.strip()

        # 3. 部分供应商把真实 JSON 错误体以字符串塞进 error.message ——
        # 拆开取内层 code（如 Responses API 的 ``invalid_encrypted_content``）
        message = error_obj.get("message")
        if isinstance(message, str) and message.strip().startswith("{"):
            inner = safe_json_loads(message)
            nested_code = _code_from_payload(inner)
            if nested_code:
                return nested_code

    # 4. 顶层 body["code"] / body["error_code"]（部分供应商的扁平 schema）
    code = body.get("code") or body.get("error_code") or ""
    if isinstance(code, str | int):
        text = str(code).strip()
        if text and text != "400":
            return text
    return ""


def _extract_message(error: Exception, body: dict) -> str:
    """提取最具信息量的错误消息（2000 字符上限 —— 部分 Anthropic 4xx 消息很长）。"""
    if body:
        error_obj = body.get("error", {})
        if isinstance(error_obj, dict):
            msg = error_obj.get("message", "")
            if isinstance(msg, str) and msg.strip():
                return msg.strip()[:2000]
        msg = body.get("message", "")
        if isinstance(msg, str) and msg.strip():
            return msg.strip()[:2000]
    return redact_sensitive_text(str(error))[:2000]


_RESET_HEADER_PATTERN = re.compile(r"^\d+(\.\d+)?$")


def _extract_suggested_delay(error: Exception) -> float | None:
    """从响应头中提取 rate limit reset / retry-after 延迟（秒）。"""
    response = getattr(error, "response", None)
    if response is None:
        return None
    headers = getattr(response, "headers", {})
    if not hasattr(headers, "get"):
        return None
    reset_str = headers.get("x-ratelimit-reset") or headers.get("retry-after")
    if not reset_str:
        return None
    try:
        return _parse_delay(str(reset_str).strip().lower())
    except Exception:
        return None


def _parse_delay(value: str) -> float | None:
    """解析带可选单位后缀（ms/s/m/h）或裸数字的延迟字符串。"""
    if value.endswith("ms"):
        return float(value[:-2]) / 1000.0
    if value.endswith("s"):
        return float(value[:-1])
    if value.endswith("m"):
        return float(value[:-1]) * 60.0
    if value.endswith("h"):
        return float(value[:-1]) * 3600.0
    if _RESET_HEADER_PATTERN.match(value):
        return float(value)
    return None
