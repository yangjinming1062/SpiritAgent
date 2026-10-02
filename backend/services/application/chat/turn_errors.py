"""回合失败的 error 帧：``message`` 面向用户，按会话语言本地化且不含内部枚举；``detail`` 是英文诊断串，只供无头消费者（委派、主动回合、IM）与日志使用，不下发客户端。"""

from components import resolve_prompt_text

from services.infrastructure.llm import FailoverReason, LLMRuntimeError, MissingLlmConfigError, MissingVideoModelError

from .chat_emitter import Emitter

_CONVERSATION_UNAVAILABLE_TEXTS: dict[str, str] = {
    "zh": "该会话已不可用，请新建会话后重试",
    "en": "This conversation is no longer available, please start a new one",
}
_TURN_LIMIT_TEXTS: dict[str, str] = {
    "zh": "本轮的工具调用次数已达上限，已停止执行",
    "en": "The tool call limit for this turn was reached, so the turn was stopped",
}
_MISSING_CONFIG_TEXTS: dict[str, str] = {
    "zh": "模型服务暂未配置，请联系管理员",
    "en": "The model service is not configured yet, please contact the administrator",
}
_MISSING_VIDEO_MODEL_TEXTS: dict[str, str] = {
    "zh": "当前会话包含视频，但没有可用的视频理解模型，请联系管理员或新建不含视频的会话",
    "en": "This conversation contains video, but no video-capable model is available, please contact the administrator or start a new conversation without video",
}
_RESPONSE_FAILED_TEXTS: dict[str, str] = {
    "zh": "模型响应未能完成，请重试",
    "en": "The model response could not be completed, please try again",
}

_ACCESS_TEXTS: dict[str, str] = {
    "zh": "模型服务暂不可用，请联系管理员",
    "en": "The model service is unavailable, please contact the administrator",
}
_BUSY_TEXTS: dict[str, str] = {
    "zh": "模型服务暂时无法响应，请稍后重试",
    "en": "The model service is not responding right now, please try again later",
}
_TOO_LARGE_TEXTS: dict[str, str] = {
    "zh": "对话内容或附件超出模型可处理的范围，请精简内容或新建会话后重试",
    "en": "The conversation or its attachments are too large for the model, please shorten them or start a new conversation",
}
_BLOCKED_TEXTS: dict[str, str] = {
    "zh": "请求被模型服务的安全策略拦截，请调整内容后重试",
    "en": "The request was blocked by the model service's safety policy, please adjust the content and try again",
}
_ATTACHMENT_FETCH_TEXTS: dict[str, str] = {
    "zh": "模型服务无法获取本次对话附带的媒体文件，文件可能已过期或无法访问，请重新上传",
    "en": "The model service couldn't fetch the media file attached to this turn, the file may have expired or be inaccessible, please upload it again",
}
_ATTACHMENT_FETCH_DETAIL = "The LLM provider couldn't fetch the media file attached to this turn. The file may have expired or the URL may not be publicly accessible. Try re-uploading the file."
_CALL_FAILED_TEXTS: dict[str, str] = {
    "zh": "模型调用失败，请重试",
    "en": "The model call failed, please try again",
}
# 未列出的失败原因（请求格式、无结果、未知）用 _CALL_FAILED_TEXTS。
_LLM_FAILURE_TEXTS: dict[FailoverReason, dict[str, str]] = {
    FailoverReason.auth: _ACCESS_TEXTS,
    FailoverReason.billing: _ACCESS_TEXTS,
    FailoverReason.model_not_found: _ACCESS_TEXTS,
    FailoverReason.provider_policy_blocked: _ACCESS_TEXTS,
    FailoverReason.rate_limit: _BUSY_TEXTS,
    FailoverReason.overloaded: _BUSY_TEXTS,
    FailoverReason.server_error: _BUSY_TEXTS,
    FailoverReason.timeout: _BUSY_TEXTS,
    FailoverReason.context_overflow: _TOO_LARGE_TEXTS,
    FailoverReason.payload_too_large: _TOO_LARGE_TEXTS,
    FailoverReason.image_too_large: _TOO_LARGE_TEXTS,
    FailoverReason.content_policy_blocked: _BLOCKED_TEXTS,
}


async def _emit(emitter: Emitter, message: str, detail: str, retry_message_id: int | None) -> None:
    await emitter.send_json(
        {"type": "error", "message": message, "detail": detail, "retry_message_id": retry_message_id},
    )


async def _emit_localized(
    emitter: Emitter,
    texts: dict[str, str],
    language: str | None,
    detail: str,
    retry_message_id: int | None,
) -> None:
    await _emit(emitter, resolve_prompt_text(texts, language), detail, retry_message_id)


async def emit_conversation_unavailable(emitter: Emitter, language: str | None, detail: str) -> None:
    """会话缺失或作用域无效：回合尚未装配，没有用户消息可重试。"""
    await _emit_localized(emitter, _CONVERSATION_UNAVAILABLE_TEXTS, language, detail, None)


async def emit_turn_limit(
    emitter: Emitter,
    language: str | None,
    max_loop_turns: int,
    *,
    retry_message_id: int | None,
) -> None:
    detail = f"Max tool execution turns ({max_loop_turns}) reached. Terminating loop to prevent unbounded execution."
    await _emit_localized(emitter, _TURN_LIMIT_TEXTS, language, detail, retry_message_id)


async def emit_llm_error(
    emitter: Emitter,
    exc: LLMRuntimeError,
    language: str | None,
    *,
    retry_message_id: int | None,
) -> None:
    """LLM 调用失败：按失败原因给出引导语，供应商消息（已脱敏）附在其后；失败原因枚举值只进 ``detail``。attachment_fetch_failed 只给简短说明，避免暴露内部细节。"""
    classified = exc.classified
    if classified.reason == FailoverReason.attachment_fetch_failed:
        await _emit_localized(emitter, _ATTACHMENT_FETCH_TEXTS, language, _ATTACHMENT_FETCH_DETAIL, retry_message_id)
        return
    message = resolve_prompt_text(_LLM_FAILURE_TEXTS.get(classified.reason, _CALL_FAILED_TEXTS), language)
    if classified.message:
        message = f"{message} — {classified.message}"
    detail = f"LLM call failed: {classified.reason.value} — {classified.message}"
    await _emit(emitter, message, detail, retry_message_id)


async def emit_llm_unavailable(
    emitter: Emitter,
    exc: MissingLlmConfigError | RuntimeError,
    language: str | None,
    *,
    retry_message_id: int | None,
) -> None:
    """配置缺失（含会话带视频却没有视频理解模型）或响应未正常完成。RuntimeError 的文本来自供应商或内部状态，只进 ``detail``。"""
    if isinstance(exc, MissingVideoModelError):
        texts = _MISSING_VIDEO_MODEL_TEXTS
    elif isinstance(exc, MissingLlmConfigError):
        texts = _MISSING_CONFIG_TEXTS
    else:
        texts = _RESPONSE_FAILED_TEXTS
    await _emit_localized(emitter, texts, language, f"LLM unavailable: {exc}", retry_message_id)
