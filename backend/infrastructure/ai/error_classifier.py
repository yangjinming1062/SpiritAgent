import enum
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import httpx
import openai

from infrastructure.ai.providers.base import ProviderError
from infrastructure.ai.providers.http import REQUEST_VALIDATION_PATTERNS, ProviderResultUnknownError
from infrastructure.logger import get_logger
from infrastructure.redact import redact_sensitive_text
from infrastructure.text import safe_json_loads

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
    result_download_failed = "result_download_failed"  # 生成已完成但成品下载失败，不得通过重新生成恢复
    context_overflow = "context_overflow"  # 上下文超限
    payload_too_large = "payload_too_large"  # 413
    image_too_large = "image_too_large"  # 单图超出供应商限制
    model_not_found = "model_not_found"  # 模型无效或不接受图像/视频输入
    provider_policy_blocked = "provider_policy_blocked"  # 聚合商账号级数据策略屏蔽唯一端点
    content_policy_blocked = "content_policy_blocked"  # 供应商安全过滤拒绝该 prompt，对同一请求确定
    format_error = "format_error"  # 请求参数或响应结构不可用
    attachment_fetch_failed = "attachment_fetch_failed"  # 供应商拉取 image_url 失败，需提示用户
    empty_result = "empty_result"  # 成功响应但无产物（如零张图）
    unknown = "unknown"


@dataclass(frozen=True)
class ClassifiedError:
    reason: FailoverReason
    status_code: int | None
    message: str

    @property
    def should_fallback(self) -> bool:
        return self.reason not in {FailoverReason.result_unknown, FailoverReason.result_download_failed}


class LLMRuntimeError(Exception):
    """携带分类结果的 LLM 调用失败；原异常在 ``__cause__``。"""

    def __init__(self, classified: ClassifiedError) -> None:
        self.classified = classified
        super().__init__(classified.message or classified.reason.value)


class LlmCallBlockedError(RuntimeError):
    """业务守卫阻止新模型调用；不按供应商故障回退或降级。"""


_BILLING_PATTERNS = (
    "insufficient credits",
    "credit balance",
    "credits exhausted",
    "exceeded your current quota",
    "plan does not include",
    "key limit exceeded",
    "token plan",
    "用量上限",
    "购买积分",
)

_RATE_LIMIT_MESSAGE_PATTERNS = (
    "rate limit",
    "rate_limit",
    "too many requests",
    "try again in",
    "please retry after",
)

# 供应商拉取 image_url 失败：须早于 format_error 匹配，以免用户被 400 文案误导
_ATTACHMENT_FETCH_PATTERNS = (
    "unable to fetch image from url",
    "unable to fetch the image",
    "could not fetch image",
    "error fetching image",
    "failed to download image from url",
)

# 用量上限消歧：带瞬时信号的是周期配额（限流），否则是计费耗尽
_USAGE_LIMIT_PATTERNS = ("usage limit", "quota", "limit exceeded")
_USAGE_LIMIT_TRANSIENT_SIGNALS = ("try again", "resets at", "reset in", "requests remaining", "periodic")

_PAYLOAD_TOO_LARGE_PATTERNS = (
    "request entity too large",
    "payload too large",
    "error code: 413",
)  # 代理/后端把状态码嵌入错误信息

# 多数供应商在整请求超 413 前先以 400 + 单图过大提示返回
_IMAGE_TOO_LARGE_PATTERNS = (
    "image exceeds",
    "image too large",
    "image_too_large",
    "image size exceeds",
    "image dimensions exceed",
    "max allowed size: 8000",
)

_EMPTY_IMAGE_RESULT_PATTERNS = ("returned no images",)

# 模型存在但拒绝图像/视频输入——换到具备该能力的供应商
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

# SDK 不区分上下文溢出子类，跨供应商（含本地服务）只能靠消息消歧
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

# 聚合商（OpenRouter）账号隐私设置排除唯一端点：模型存在，换家也被同一账号设置拦截
_PROVIDER_POLICY_BLOCKED_PATTERNS = (
    "no endpoints available matching your guardrail",
    "no endpoints available matching your data policy",
    "no endpoints found matching your data policy",
)

# 供应商对单条 prompt 的安全判断，对同一请求确定；模式收窄以免误撞计费/鉴权/格式错误
_CONTENT_POLICY_BLOCKED_PATTERNS = (
    "flagged for possible cybersecurity risk",
    "trusted access for cyber",
    "violates our usage policies",
    "violates openai's usage policies",
    "your request was flagged by",
    "prompt was flagged by our safety",
    "responses cannot be generated due to safety",
    "violated safety policy",  # MiniMax base_resp 1027
    "new_sensitive",  # MiniMax base_resp 1026
    "content_filter",  # OpenAI 标准 token；不匹配带空格的 "content filter"（良性配置描述）
    "responsibleaipolicyviolation",
    "image_safety",  # Gemini 生图 IMAGE_SAFETY
    "generated image rejected by content moderation",  # Grok 生图审核拒绝。
    "generative ai prohibited use policy",
    "input data may contain inappropriate content",
    "output data may contain inappropriate content",
    "input or output data may contain inappropriate content",
    "request blocked due to safety violations",
)

_CONTENT_POLICY_ERROR_CODES = frozenset(
    {
        "content_filter",
        "content_policy_violation",
        "content_policy_blocked",
        "responsibleaipolicyviolation",
        "datainspectionfailed",  # 千问原生错误码。
        "data_inspection_failed",  # 千问兼容协议错误码。
        "1026",  # MiniMax 输入审核。
        "1027",  # MiniMax 输出审核。
        "safety",
        "jailbreak",
        "prohibited_content",
        "blocklist",
        "image_safety",
    },
)
_CONTENT_POLICY_FINISH_REASONS = frozenset(
    {
        "content_filter",
        "refusal",
        "safety",
        "recitation",
        "blocklist",
        "prohibited_content",
        "spii",
        "image_safety",
        "image_prohibited_content",
        "image_recitation",
    },
)
_POLICY_DIAGNOSTIC_CODES = _CONTENT_POLICY_ERROR_CODES - {"safety", "jailbreak", "blocklist", "1026", "1027"}

_GOOGLE_POLICY_RESPONSE = re.compile(
    r"The prompt could not be submitted\.\s+"
    r"The prompt contains sensitive words that violate Google's "
    r"(?:Generative AI Prohibited Use Policy|\[Generative AI Prohibited Use Policy\]"
    r"\(https://policies\.google\.com/terms/generative-ai/use-policy/?\))\.\s+"
    r"Try rephrasing the prompt\."
    r"(?:\s+If you think this was an error, "
    r"(?:send feedback|\[send feedback\]\(https://ai\.google\.dev/gemini-api/docs/troubleshooting/?\))\.)?",
    re.IGNORECASE,
)
_POLICY_DIAGNOSTIC_CODE = re.compile(
    r"(?:(?P<prefix>(?:[\w.-]+\s+)?(?:api\s+)?(?:error|badrequesterror)(?:\s+code)?)\s*:?\s*)?"
    r"(?P<code>[\w]+)(?:\s*[:：-]\s*(?:(?:input|output)\s+)?(?:blocked|rejected))?[.!]?",
    re.IGNORECASE,
)
_POLICY_DIAGNOSTIC_MESSAGES = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"(?:(?:Error|BadRequestError)(?: code)?\s*:\s*(?:400\s*-\s*)?)?"
        r"(?:Input|Output|Input or output) data may contain inappropriate content\."
        r"(?:\s*For details, see:\s*https://help\.aliyun\.com/zh/model-studio/error-code/?#inappropriate-content\.?)?",
        r"(?:(?:minimax\s+)?error\s+(?:1026|1027):\s*)?(?:input|output)\s+"
        r"(?:new_sensitive|violated safety policy)\.?",
        r"(?:Your|The) (?:request|prompt) (?:was flagged by our safety system|violates our usage policies)\."
        r"(?:\s*Please (?:modify your (?:input|prompt) and (?:retry|try again)|try again with a different prompt)\.)?",
        r"Responses cannot be generated due to safety filters\.",
        r"The request was rejected because it was considered high risk\.?",
        r"Request blocked due to safety violations(?: \(harmful content\))?\."
        r"(?:\s*Please modify your input and retry\.)?",
        r"The prompt contains sensitive words that violate Google's Generative AI Prohibited Use Policy\.",
    )
)

# xAI 订阅 entitlement：SSE type=error 不带状态码，须先于通用分类拦截
_GROK_ENTITLEMENT_PATTERN = "do not have an active grok subscription"

_TIMEOUT_MESSAGE_PATTERNS = (
    "timed out",
    "deadline exceeded",
)  # 无异常类型信号时的超时文案（如本地 shim 的 RuntimeError）

# 无状态码的服务端断开：大会话按上下文溢出处理，否则视为传输超时
_SERVER_DISCONNECT_PATTERNS = (
    "server disconnected",
    "peer closed connection",
    "connection reset by peer",
    "connection was closed",
    "network connection lost",
    "unexpected eof",
)

# SSL/TLS 瞬时失败的消息兜底（ssl.SSLError 已按 OSError 归为超时）
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

# 已收到 HTTP 响应的异常类型：状态码优先于结构化错误码
_HTTP_STATUS_ERRORS = (openai.APIStatusError, httpx.HTTPStatusError, ProviderError)
_TRANSPORT_ERRORS = (openai.APIConnectionError, httpx.RequestError, OSError)


def is_content_policy_error_message(msg: str) -> bool:
    """用与 ``classify_api_error`` 相同的模式列表对原始字符串做匹配。"""
    return any(p in msg.lower() for p in _CONTENT_POLICY_BLOCKED_PATTERNS)


def _refused_message(message: dict[str, Any]) -> bool:
    if message.get("type") == "refusal":
        return isinstance(message.get("refusal"), str)
    if message.get("type") != "message" and message.get("role") != "assistant":
        return False
    refusal = message.get("refusal")
    if isinstance(refusal, str) and refusal.strip():
        return True
    content = message.get("content")
    return isinstance(content, list) and any(
        isinstance(part, dict) and part.get("type") == "refusal" for part in content
    )


def _policy_diagnostic(text: str) -> bool:
    text = text.strip()
    code = _POLICY_DIAGNOSTIC_CODE.fullmatch(text)
    return bool(
        (code and code["code"].lower() in (_CONTENT_POLICY_ERROR_CODES if code["prefix"] else _POLICY_DIAGNOSTIC_CODES))
        or _GOOGLE_POLICY_RESPONSE.fullmatch(text)
        or any(pattern.fullmatch(text) for pattern in _POLICY_DIAGNOSTIC_MESSAGES),
    )


def _policy_response(payload: dict[str, Any], *, error_context: bool, depth: int) -> bool:
    codes = (payload.get("code"), payload.get("type"), payload.get("error_code"), payload.get("status_code"))
    allowed_codes = _CONTENT_POLICY_ERROR_CODES if error_context else _POLICY_DIAGNOSTIC_CODES
    if (error_context or isinstance(payload.get("message"), str)) and any(
        _code_text(code).lower() in allowed_codes for code in codes
    ):
        return True
    message = payload.get("message")
    if error_context and isinstance(message, str) and _policy_diagnostic(message):
        return True
    details = payload.get("incomplete_details")
    if isinstance(details, dict) and details.get("reason") == "content_filter":
        return True
    if _refused_message(payload):
        return True
    output = payload.get("output")
    if isinstance(output, list) and any(isinstance(item, dict) and _refused_message(item) for item in output):
        return True
    choices = payload.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            if str(choice.get("finish_reason", "")).lower() in _CONTENT_POLICY_FINISH_REASONS:
                return True
            message = choice.get("message")
            if isinstance(message, dict) and _refused_message(message):
                return True
    feedback = payload.get("promptFeedback", payload.get("prompt_feedback"))
    if isinstance(feedback, dict) and str(feedback.get("blockReason", feedback.get("block_reason", ""))).lower() in (
        _CONTENT_POLICY_FINISH_REASONS | {"other"}
    ):
        return True
    candidates = payload.get("candidates")
    if isinstance(candidates, list) and any(
        isinstance(candidate, dict)
        and str(candidate.get("finishReason", candidate.get("finish_reason", ""))).lower()
        in _CONTENT_POLICY_FINISH_REASONS
        for candidate in candidates
    ):
        return True
    if depth == 0:
        return False
    # 只展开已知错误包装位置，最多三层；不进入业务字段或模型正文。
    wrappers = [payload.get("error"), payload.get("base_resp")]
    if error_context:
        metadata = payload.get("metadata")
        wrappers.extend(
            (
                payload.get("innererror"),
                payload.get("inner_error"),
                payload.get("message"),
                metadata.get("raw") if isinstance(metadata, dict) else None,
            ),
        )
    for wrapper in wrappers:
        if isinstance(wrapper, str):
            if _policy_diagnostic(wrapper):
                return True
            wrapper = safe_json_loads(wrapper)
        if isinstance(wrapper, dict) and _policy_response(wrapper, error_context=True, depth=depth - 1):
            return True
    return False


def is_content_policy_response(payload: dict[str, Any]) -> bool:
    """识别供应商协议中的拦截信号，错误码与通用 HTTP 状态分别判断。"""
    return _policy_response(payload, error_context=False, depth=3)


def classify_response_text(text: str) -> ClassifiedError | None:
    """识别正文中的供应商拒绝信封、诊断及完整模板，普通文字拒答不在此判定。"""
    text = text.strip()
    # 只解析完整正文或明确错误前缀后的 JSON，不从引文、代码块和业务字段中搜错。
    payload_text = re.sub(r"^Error code:\s*\d{3}\s*-\s*", "", text, flags=re.IGNORECASE)
    payload = safe_json_loads(payload_text)
    if (isinstance(payload, dict) and is_content_policy_response(payload)) or _policy_diagnostic(text):
        return ClassifiedError(
            FailoverReason.content_policy_blocked,
            None,
            "LLM provider returned a content policy error as output text",
        )
    return None


@dataclass(frozen=True)
class _Signals:
    status_code: int | None
    error_code: str
    text: str
    body_message: str
    approx_tokens: int
    context_length: int | None
    num_messages: int

    def has(self, patterns: Iterable[str]) -> bool:
        return any(p in self.text for p in patterns)

    def is_large(self, *, ratio: float) -> bool:
        return self.context_length is not None and self.approx_tokens > self.context_length * ratio


def classify_api_error(
    error: BaseException,
    *,
    approx_tokens: int = 0,
    context_length: int | None = None,
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
        reason=_classify(error, signals, body),
        status_code=signals.status_code,
        # 消息会进入对外错误与日志，供应商响应体同样可能回显密钥
        message=redact_sensitive_text(_body_message(body) or str(error) or "")[:2000],
    )


def _classify(error: BaseException, s: _Signals, body: dict) -> FailoverReason:
    # SDK 会把响应丢失包装成连接异常，仍须保留禁止重发的语义。
    current = error
    for _ in range(_CAUSE_CHAIN_MAX_DEPTH):
        if isinstance(current, ProviderResultUnknownError):
            return FailoverReason.result_unknown
        cause = current.__cause__ or current.__context__
        if cause is None or cause is current:
            break
        current = cause
    # 内容策略须早于状态码：避免 400 安全拦截降级为 format_error、无状态码拦截落入 unknown
    if (
        s.error_code in _CONTENT_POLICY_ERROR_CODES
        or _policy_response(body, error_context=True, depth=3)
        or s.has(_CONTENT_POLICY_BLOCKED_PATTERNS)
    ):
        return FailoverReason.content_policy_blocked
    if _GROK_ENTITLEMENT_PATTERN in s.text or ("out of available resources" in s.text and "grok" in s.text):
        return FailoverReason.auth
    # 响应体校验失败（APIError 子类，非 APIStatusError）：状态码取自 .response
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

    # 断连须早于 SSL 兜底：大会话按上下文溢出处理
    if s.has(_SERVER_DISCONNECT_PATTERNS):
        if s.is_large(ratio=0.6):
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
            # 部分网关以 5xx 返回请求校验错误，按确定性格式错误处理以免重试风暴
            if s.has(REQUEST_VALIDATION_PATTERNS) or s.error_code in _REQUEST_VALIDATION_ERROR_CODES:
                return FailoverReason.format_error
            if s.has(_BILLING_PATTERNS) or s.error_code in _BILLING_ERROR_CODES or s.error_code == "2056":
                return FailoverReason.billing
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
    # 部分供应商以 404 返回免费档付费模型失效，按计费处理以展示充值指引
    if s.has(_BILLING_PATTERNS):
        return FailoverReason.billing
    if s.has(_PROVIDER_POLICY_BLOCKED_PATTERNS):
        return FailoverReason.provider_policy_blocked
    if s.has(_MODEL_NOT_FOUND_PATTERNS) or s.has(_VISION_UNSUPPORTED_PATTERNS):
        return FailoverReason.model_not_found
    # 无模型缺失信号的 404 可能是端点路径错配（本地服务）或代理抖动
    return FailoverReason.unknown


def _by_400(s: _Signals) -> FailoverReason:
    # 不支持视觉早于单图过大：恢复路径不同
    if s.has(_VISION_UNSUPPORTED_PATTERNS):
        return FailoverReason.model_not_found
    # 单图过大早于上下文溢出：消息可能同时命中两类模式
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
    # 大会话上消息极短（如裸 "error"）的 400 多为上下文过大
    if len(s.body_message) < 30 and s.is_large(ratio=0.4):
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
    """供模式匹配的小写全文：str(error) 不一定含响应体消息；OpenRouter 把上游真实错误包在 error.metadata.raw 的 JSON 字符串里，一并展开。"""
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
    """沿异常链查找 HTTP 状态码：.status_code / .status / .response.status_code。"""
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
    """结构化错误体：.body 为 dict 时直接用，否则解析 .response.json()。"""
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
    """读取 error.code/type、message 内嵌错误及顶层错误码。"""
    err = payload.get("error")
    if isinstance(err, dict):
        code = err.get("code") or err.get("type")
        if isinstance(code, str | int) and (text := _code_text(code)):
            return text
        message = err.get("message")
        if unwrap_message and isinstance(message, str) and message.strip().startswith("{"):
            inner = safe_json_loads(message)
            if isinstance(inner, dict) and (text := _payload_code(inner, unwrap_message=False)):
                return text
    return _code_text(payload.get("code") or payload.get("error_code"))


def _extract_error_code(error: BaseException, body: dict) -> str:
    """结构化错误码：异常自身 .code / .type（OpenAI SDK）优先，其次响应体。"""
    for attr in ("code", "type"):
        value = getattr(error, attr, None)
        if isinstance(value, str) and (text := _code_text(value)):
            return text
    return _payload_code(body, unwrap_message=True)
