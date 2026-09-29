import enum
from collections.abc import Iterable
from dataclasses import dataclass

import httpx
import openai
from components import get_logger, redact_sensitive_text, safe_json_loads

from .providers.base import ProviderError
from .providers.http import REQUEST_VALIDATION_PATTERNS, ProviderResultUnknownError

logger = get_logger(__name__)

_CAUSE_CHAIN_MAX_DEPTH = 5


class FailoverReason(enum.Enum):
    """API 调用失败原因；值经错误信封对外暴露。"""

    auth = "auth"  # 401/403 鉴权失败
    billing = "billing"  # 402 或确认的额度耗尽
    rate_limit = "rate_limit"  # 429 或周期配额限流
    overloaded = "overloaded"  # 503/529 供应商过载
    server_error = "server_error"  # 500/502 内部错误
    timeout = "timeout"  # 连接/读取超时或断连
    result_unknown = "result_unknown"  # 非幂等请求可能已生效 —— 禁止自动重试或回退
    context_overflow = "context_overflow"  # 上下文超限 —— 换家无效
    payload_too_large = "payload_too_large"  # 413
    image_too_large = "image_too_large"  # 单图超出供应商限制
    model_not_found = "model_not_found"  # 模型无效或不接受图像/视频输入
    provider_policy_blocked = "provider_policy_blocked"  # 聚合商账号级数据策略屏蔽唯一端点，换家同样被拦
    content_policy_blocked = "content_policy_blocked"  # 供应商安全过滤拒绝该 prompt，对同一请求确定
    format_error = "format_error"  # 请求畸形，重试结果相同
    attachment_fetch_failed = "attachment_fetch_failed"  # 供应商拉取 image_url 失败，需提示用户
    empty_result = "empty_result"  # 成功响应但无产物（如零张图）
    unknown = "unknown"


# 可切换到链中下一家的原因：确定性失败换家可能成功；超时/过载在本家传输层重试耗尽后才到这里。
# server_error 通常是供应商特定行为、unknown 无信号，均不级联。
_FALLBACK_REASONS = frozenset(
    {
        FailoverReason.auth,
        FailoverReason.billing,
        FailoverReason.rate_limit,
        FailoverReason.overloaded,
        FailoverReason.timeout,
        FailoverReason.model_not_found,
        FailoverReason.content_policy_blocked,
        FailoverReason.format_error,
        FailoverReason.empty_result,
    },
)


@dataclass(frozen=True)
class ClassifiedError:
    reason: FailoverReason
    status_code: int | None
    message: str

    @property
    def should_fallback(self) -> bool:
        return self.reason in _FALLBACK_REASONS


class LLMRuntimeError(Exception):
    """携带分类结果的 LLM 调用失败；原异常在 ``__cause__``。"""

    def __init__(self, classified: ClassifiedError) -> None:
        self.classified = classified
        super().__init__(classified.message or classified.reason.value)


_BILLING_PATTERNS = (
    "insufficient credits",
    "credit balance",
    "credits exhausted",
    "exceeded your current quota",
    "plan does not include",
    "key limit exceeded",
)

_RATE_LIMIT_MESSAGE_PATTERNS = (
    "rate limit",
    "rate_limit",
    "too many requests",
    "try again in",
    "please retry after",
)

# 供应商无法拉取 image_url 中的图像 URL：须早于 format_error 匹配，以免用户看到被 400 文案误导。
_ATTACHMENT_FETCH_PATTERNS = (
    "unable to fetch image from url",
    "unable to fetch the image",
    "could not fetch image",
    "error fetching image",
    "failed to download image from url",
)

# 用量上限需消歧：带瞬时信号的是周期配额（限流），否则是计费耗尽。
_USAGE_LIMIT_PATTERNS = ("usage limit", "quota", "limit exceeded")
_USAGE_LIMIT_TRANSIENT_SIGNALS = ("try again", "resets at", "reset in", "requests remaining", "periodic")

# 代理或后端把 HTTP 状态码嵌入错误信息的 payload 过大。
_PAYLOAD_TOO_LARGE_PATTERNS = ("request entity too large", "payload too large", "error code: 413")

# 多数供应商在整请求超 413 前先以 400 + 单图过大提示返回。
_IMAGE_TOO_LARGE_PATTERNS = (
    "image exceeds",
    "image too large",
    "image_too_large",
    "image size exceeds",
    "image dimensions exceed",
    "max allowed size: 8000",
)

_EMPTY_IMAGE_RESULT_PATTERNS = ("returned no images",)

# 模型存在但拒绝图像/视频输入 —— 换到具备该能力的供应商。
_VISION_UNSUPPORTED_PATTERNS = (
    "no endpoints found that support image input",
    "does not support image input",
    "image input not supported",
    "does not support vision",
    "multimodal input not supported",
    "does not support multimodal",
    "input_video",  # Responses 网关拒收视频项（mimo: "input item type 'input_video' is not supported"）
    "video input not supported",
    "does not support video",
)

# SDK 不区分上下文溢出子类，跨供应商（含 vLLM / Ollama / llama.cpp 等本地服务）只能靠消息消歧。
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
    "max_model_len",
    "prompt length",
    "input is too long",
    "maximum model length",
    "truncating input",
    "slot context",
    "n_ctx_slot",
    "超过最大长度",
    "上下文长度",
    "max input token",
    "exceeds the maximum number of input tokens",
)

_MODEL_NOT_FOUND_PATTERNS = ("is not a valid model", "invalid model", "model not found", "model_not_found")

# 聚合商（OpenRouter）账号隐私设置排除唯一端点：模型存在，换家也被同一账号设置拦截。
_PROVIDER_POLICY_BLOCKED_PATTERNS = (
    "no endpoints available matching your guardrail",
    "no endpoints available matching your data policy",
    "no endpoints found matching your data policy",
)

# 供应商对单条 prompt 的安全判断，对同一请求确定；模式收窄以免误撞计费 / 鉴权 / 格式错误。
_CONTENT_POLICY_BLOCKED_PATTERNS = (
    "flagged for possible cybersecurity risk",
    "trusted access for cyber",
    "violates our usage policies",
    "violates openai's usage policies",
    "your request was flagged by",
    "prompt was flagged by our safety",
    "responses cannot be generated due to safety",
    # MiniMax base_resp 1027 安全拒绝原文
    "violated safety policy",
    # MiniMax base_resp 1026 敏感输入审核原文
    "new_sensitive",
    # ``content_filter`` 是 OpenAI 标准 token；不匹配带空格的 "content filter"，后者出现在良性配置描述中
    "content_filter",
    "responsibleaipolicyviolation",
    # Gemini 生图 IMAGE_SAFETY
    "image_safety",
    "generative ai prohibited use policy",
)

# xAI 订阅 entitlement：SSE ``type=error`` 不带状态码，须先于通用分类拦截。
_GROK_ENTITLEMENT_PATTERN = "do not have an active grok subscription"

# 无异常类型信号时的超时文案（如本地 shim 用 RuntimeError 包装子进程超时）。
_TIMEOUT_MESSAGE_PATTERNS = ("timed out", "deadline exceeded")

# 无状态码的服务端断开：大会话按上下文溢出处理，否则视为传输超时。
_SERVER_DISCONNECT_PATTERNS = (
    "server disconnected",
    "peer closed connection",
    "connection reset by peer",
    "connection was closed",
    "network connection lost",
    "unexpected eof",
)

# SSL/TLS 瞬时失败的消息兜底（ssl.SSLError 已按 OSError 归为超时）。
_SSL_TRANSIENT_PATTERNS = ("bad record mac", "ssl handshake failure", "tlsv1 alert", "[ssl:")

_REQUEST_VALIDATION_ERROR_CODES = frozenset({"invalid_request_error", "unknown_parameter", "unsupported_parameter"})
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

# 已收到 HTTP 响应的异常类型：状态码优先于结构化错误码。
_HTTP_STATUS_ERRORS = (openai.APIStatusError, httpx.HTTPStatusError, ProviderError)
_TRANSPORT_ERRORS = (openai.APIConnectionError, httpx.RequestError, OSError)


def is_content_policy_error_message(msg: str) -> bool:
    """用与 ``classify_api_error`` 相同的模式列表对原始字符串做匹配。"""
    return any(p in msg.lower() for p in _CONTENT_POLICY_BLOCKED_PATTERNS)


@dataclass(frozen=True)
class _Signals:
    status_code: int | None
    error_code: str
    text: str
    body_message: str
    approx_tokens: int
    context_length: int
    num_messages: int

    def has(self, patterns: Iterable[str]) -> bool:
        return any(p in self.text for p in patterns)

    def is_large(self, *, ratio: float, tokens: int, messages: int) -> bool:
        # 绝对 token / 消息数阈值只是小上下文窗口的近似。
        return self.approx_tokens > self.context_length * ratio or (
            self.context_length <= 256000 and (self.approx_tokens > tokens or self.num_messages > messages)
        )


def classify_api_error(
    error: BaseException,
    *,
    approx_tokens: int = 0,
    context_length: int = 200000,
    num_messages: int = 0,
) -> ClassifiedError:
    """把供应商调用异常分类为失败原因；已携带分类结果的异常直接返回其结果。"""
    classified = getattr(error, "classified", None)
    if isinstance(classified, ClassifiedError):
        return classified
    body = _extract_error_body(error)
    signals = _Signals(
        status_code=_extract_status_code(error),
        error_code=_extract_error_code(error, body).lower(),
        text=_build_error_message(error, body),
        body_message=_body_message(body).lower(),
        approx_tokens=approx_tokens,
        context_length=context_length,
        num_messages=num_messages,
    )
    return ClassifiedError(
        reason=_classify(error, signals),
        status_code=signals.status_code,
        message=(_body_message(body) or redact_sensitive_text(str(error)) or "")[:2000],
    )


def _classify(error: BaseException, s: _Signals) -> FailoverReason:
    # 内容策略须早于状态码：避免 400 安全拦截降级为 format_error、无状态码拦截落入 unknown。
    if s.has(_CONTENT_POLICY_BLOCKED_PATTERNS):
        return FailoverReason.content_policy_blocked
    if _GROK_ENTITLEMENT_PATTERN in s.text or ("out of available resources" in s.text and "grok" in s.text):
        return FailoverReason.auth
    if isinstance(error, ProviderResultUnknownError):
        return FailoverReason.result_unknown
    # 响应体校验失败（APIError 子类，非 APIStatusError）：状态码取自 .response。
    if isinstance(error, openai.APIResponseValidationError):
        if s.status_code is not None and s.has(_CONTEXT_OVERFLOW_PATTERNS):
            return FailoverReason.context_overflow
        return FailoverReason.format_error
    if isinstance(error, _TRANSPORT_ERRORS):
        return FailoverReason.timeout

    rules = (_by_status, _by_error_code) if isinstance(error, _HTTP_STATUS_ERRORS) else (_by_error_code, _by_status)
    for rule in rules:
        if (reason := rule(s)) is not None:
            return reason
    if (reason := _by_message(s)) is not None:
        return reason

    # 断连须早于 SSL 兜底：大会话按上下文溢出处理。
    if s.has(_SERVER_DISCONNECT_PATTERNS):
        if s.is_large(ratio=0.6, tokens=120000, messages=200):
            return FailoverReason.context_overflow
        return FailoverReason.timeout
    if s.has(_SSL_TRANSIENT_PATTERNS):
        return FailoverReason.timeout
    return FailoverReason.unknown


def _usage_limit_reason(s: _Signals) -> FailoverReason:
    return FailoverReason.rate_limit if s.has(_USAGE_LIMIT_TRANSIENT_SIGNALS) else FailoverReason.billing


def _by_status(s: _Signals) -> FailoverReason | None:
    match s.status_code:
        case None:
            return None
        case 401:
            return FailoverReason.auth
        case 402:
            return _usage_limit_reason(s) if s.has(_USAGE_LIMIT_PATTERNS) else FailoverReason.billing
        case 403:
            if "spending limit" in s.text or s.has(_BILLING_PATTERNS):
                return FailoverReason.billing
            return FailoverReason.auth
        case 404:
            return _by_404(s)
        case 413:
            return FailoverReason.payload_too_large
        case 429:
            return FailoverReason.rate_limit
        case 400:
            return _by_400(s)
        case 500 | 502:
            # 部分 OpenAI 兼容网关以 5xx 返回请求校验错误，按确定性格式错误处理以免重试风暴。
            if s.has(REQUEST_VALIDATION_PATTERNS) or s.error_code in _REQUEST_VALIDATION_ERROR_CODES:
                return FailoverReason.format_error
            return FailoverReason.server_error
        case 503 | 529:
            return FailoverReason.overloaded
        case code if 400 <= code < 500:
            return FailoverReason.format_error
        case code if 500 <= code < 600:
            return FailoverReason.server_error
        case _:
            return None


def _by_404(s: _Signals) -> FailoverReason:
    # 部分供应商以 404 返回免费档付费模型失效，按计费处理以展示充值指引。
    if s.has(_BILLING_PATTERNS):
        return FailoverReason.billing
    if s.has(_PROVIDER_POLICY_BLOCKED_PATTERNS):
        return FailoverReason.provider_policy_blocked
    if s.has(_MODEL_NOT_FOUND_PATTERNS) or s.has(_VISION_UNSUPPORTED_PATTERNS):
        return FailoverReason.model_not_found
    # 无模型缺失信号的 404 可能是端点路径错配（本地服务）或代理抖动。
    return FailoverReason.unknown


def _by_400(s: _Signals) -> FailoverReason:
    # 不支持视觉早于单图过大：恢复路径不同。
    if s.has(_VISION_UNSUPPORTED_PATTERNS):
        return FailoverReason.model_not_found
    # 单图过大早于上下文溢出：消息可能同时命中两类模式。
    if s.has(_IMAGE_TOO_LARGE_PATTERNS):
        return FailoverReason.image_too_large
    if s.error_code in _CONTEXT_OVERFLOW_ERROR_CODES or s.has(_CONTEXT_OVERFLOW_PATTERNS):
        return FailoverReason.context_overflow
    if s.has(_PROVIDER_POLICY_BLOCKED_PATTERNS):
        return FailoverReason.provider_policy_blocked
    if s.has(_MODEL_NOT_FOUND_PATTERNS):
        return FailoverReason.model_not_found
    if s.has(_RATE_LIMIT_MESSAGE_PATTERNS):
        return FailoverReason.rate_limit
    if s.has(_BILLING_PATTERNS):
        return FailoverReason.billing
    if s.has(_ATTACHMENT_FETCH_PATTERNS):
        return FailoverReason.attachment_fetch_failed
    # 大会话上消息极短（如裸 "error"）的 400 多为上下文过大。
    if len(s.body_message) < 30 and s.is_large(ratio=0.4, tokens=80000, messages=80):
        return FailoverReason.context_overflow
    return FailoverReason.format_error


def _by_error_code(s: _Signals) -> FailoverReason | None:
    if s.error_code in _RATE_LIMIT_ERROR_CODES:
        return FailoverReason.rate_limit
    if s.error_code in _BILLING_ERROR_CODES:
        return FailoverReason.billing
    if s.error_code in _MODEL_NOT_FOUND_ERROR_CODES:
        return FailoverReason.model_not_found
    if s.error_code in _CONTEXT_OVERFLOW_ERROR_CODES:
        return FailoverReason.context_overflow
    return None


def _by_message(s: _Signals) -> FailoverReason | None:
    if s.has(_PAYLOAD_TOO_LARGE_PATTERNS):
        return FailoverReason.payload_too_large
    if s.has(_IMAGE_TOO_LARGE_PATTERNS):
        return FailoverReason.image_too_large
    if s.has(_EMPTY_IMAGE_RESULT_PATTERNS):
        return FailoverReason.empty_result
    if s.has(_USAGE_LIMIT_PATTERNS):
        return _usage_limit_reason(s)
    if s.has(_BILLING_PATTERNS):
        return FailoverReason.billing
    if s.has(_RATE_LIMIT_MESSAGE_PATTERNS):
        return FailoverReason.rate_limit
    if s.has(_CONTEXT_OVERFLOW_PATTERNS):
        return FailoverReason.context_overflow
    if s.has(_PROVIDER_POLICY_BLOCKED_PATTERNS):
        return FailoverReason.provider_policy_blocked
    if s.has(_MODEL_NOT_FOUND_PATTERNS):
        return FailoverReason.model_not_found
    if s.has(_TIMEOUT_MESSAGE_PATTERNS):
        return FailoverReason.timeout
    return None


def _body_message(body: dict) -> str:
    """响应体中的错误消息：``error.message`` 优先，其次顶层 ``message``。"""
    err = body.get("error")
    msg = err.get("message") if isinstance(err, dict) else None
    if not (isinstance(msg, str) and msg.strip()):
        msg = body.get("message")
    return msg.strip() if isinstance(msg, str) else ""


def _build_error_message(error: BaseException, body: dict) -> str:
    """供模式匹配的小写全文：str(error) 不一定含响应体消息；OpenRouter 把上游真实错误包在
    ``error.metadata.raw`` 的 JSON 字符串里，一并展开。
    """
    text = str(error).lower()
    parts = [text]
    body_msg = _body_message(body).lower()
    if body_msg and body_msg not in text:
        parts.append(body_msg)
    err = body.get("error")
    metadata = err.get("metadata") if isinstance(err, dict) else None
    raw = metadata.get("raw") if isinstance(metadata, dict) else None
    if isinstance(raw, str) and raw.strip():
        inner = safe_json_loads(raw)
        inner_msg = _body_message(inner).lower() if isinstance(inner, dict) else ""
        if inner_msg and inner_msg not in text and inner_msg not in body_msg:
            parts.append(inner_msg)
    return " ".join(parts)


def _extract_status_code(error: BaseException) -> int | None:
    """沿异常链查找 HTTP 状态码：``.status_code`` / ``.status`` / ``.response.status_code``。"""
    current: BaseException = error
    for _ in range(_CAUSE_CHAIN_MAX_DEPTH):
        code = getattr(current, "status_code", None)
        if isinstance(code, int):
            return code
        code = getattr(current, "status", None)
        if isinstance(code, int) and 100 <= code < 600:
            return code
        code = getattr(getattr(current, "response", None), "status_code", None)
        if isinstance(code, int) and 100 <= code < 600:
            return code
        cause = current.__cause__ or current.__context__
        if cause is None or cause is current:
            break
        current = cause
    return None


def _extract_error_body(error: BaseException) -> dict:
    """结构化错误体：``.body`` 为 dict 时直接用，否则解析 ``.response.json()``。"""
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


def _code_text(value: object) -> str:
    text = str(value).strip() if isinstance(value, str | int) else ""
    return "" if text == "400" else text


def _payload_code(payload: dict, *, unwrap_message: bool) -> str:
    """``error.code`` / ``error.type`` → message 内嵌 JSON 的错误码 → 顶层 ``code`` / ``error_code``。"""
    err = payload.get("error")
    if isinstance(err, dict):
        code = err.get("code") or err.get("type")
        if isinstance(code, str) and (text := _code_text(code)):
            return text
        # 部分供应商把真实 JSON 错误体以字符串塞进 error.message。
        message = err.get("message")
        if unwrap_message and isinstance(message, str) and message.strip().startswith("{"):
            inner = safe_json_loads(message)
            if isinstance(inner, dict) and (text := _payload_code(inner, unwrap_message=False)):
                return text
    return _code_text(payload.get("code") or payload.get("error_code"))


def _extract_error_code(error: BaseException, body: dict) -> str:
    """结构化错误码：异常自身 ``.code`` / ``.type``（OpenAI SDK）优先，其次响应体。"""
    for attr in ("code", "type"):
        value = getattr(error, attr, None)
        if isinstance(value, str) and (text := _code_text(value)):
            return text
    return _payload_code(body, unwrap_message=True)
