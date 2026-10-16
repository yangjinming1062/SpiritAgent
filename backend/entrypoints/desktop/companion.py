from features.companion.interaction_stats import record_interaction
from features.companion.persona_service import PersonaValidationError
from features.companion.use_cases import (
    match_voice,
    onboarding_state,
    set_user_timezone,
    submit_onboarding,
    voice_catalog,
)
from features.companion.voice_catalog import normalize_voice_language
from features.media.voice_operations import cancel_voice_operation, run_voice_operation
from features.media.voices import clone_voice, confirm_voice, design_voice
from infrastructure.ai.llm_client import MissingLlmConfigError
from infrastructure.constants import JSONRPC_INVALID_PARAMS
from infrastructure.logger import get_logger
from runtime.desktop.jsonrpc import JsonRpcError
from runtime.gateway_contracts import GatewaySession, RegisterMethod
from runtime.gateway_contracts import optional_str as _optional_str
from runtime.gateway_contracts import require_str as _require_str
from workflows.voice_design import generate_voice_prompt

logger = get_logger(__name__)


def register_handlers(session: GatewaySession, register: RegisterMethod) -> None:
    user_id = session.user_id

    async def companion_set_timezone(params: dict) -> dict:
        # Desktop 每次连接上报本地 IANA 时区：夜间批处理与互动统计按用户本地日聚合，缺这一行时整个夜间流水线会静默跳过。
        tz = params.get("timezone")
        if not isinstance(tz, str) or not tz.strip():
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "timezone must be a non-empty string")
        normalized = tz.strip()
        try:
            await set_user_timezone(user_id, normalized)
        except ValueError as exc:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, str(exc)) from exc
        return {"timezone": normalized}

    register("companion.set_timezone", companion_set_timezone)

    async def companion_record_interaction_stats(params: dict) -> dict:
        # chat_turn 每事件统计供每日 Memory 汇总（无 LLM 开销），desktop 侧合并到 STATS_THRESHOLD 后切分钟级节流；hour 是用户本地小时，与本地日期键同口径。
        kind = params.get("kind")
        hour = params.get("hour")
        if not isinstance(hour, int) or not 0 <= hour <= 23:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "hour must be int in [0, 23]")
        if kind != "chat_turn":
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"kind must be chat_turn, got {kind!r}")
        return await record_interaction(user_id, kind, hour)

    register("companion.record_interaction_stats", companion_record_interaction_stats)

    async def onboarding_get_state(_params: dict) -> dict:
        return (await onboarding_state(user_id)).model_dump()

    async def onboarding_submit(params: dict) -> dict:
        # 每字段增量落库，崩溃最多丢当前一题。
        field = params.get("field")
        if not isinstance(field, str) or not field:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "field must be a non-empty string")
        value = params.get("value")
        if value is not None and not isinstance(value, str):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "value must be a string or null")
        try:
            return (
                await submit_onboarding(user_id, field, value, expected_version=params.get("expected_version"))
            ).model_dump()
        except PersonaValidationError as exc:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, str(exc)) from exc

    register("onboarding.get_state", onboarding_get_state)

    register("onboarding.submit", onboarding_submit)

    async def tts_list_voices(params: dict) -> dict:
        # 可选 language 过滤——未知值直接返回完整目录，避免将来新增 tag 时 400。
        language = normalize_voice_language(_optional_str(params, "language"))
        return (await voice_catalog(user_id, language)).model_dump()

    async def tts_match_voice(params: dict) -> dict:
        # onboarding 不为已有目录覆盖的窄标签任务付 LLM 延迟，直接映射到已配置供应商目录中的 voice id。
        preference = _require_str(params, "preference")
        language = normalize_voice_language(_optional_str(params, "language"))
        return (await match_voice(user_id, preference, language)).model_dump()

    async def tts_design_voice(params: dict) -> dict:
        try:
            result = await run_voice_operation(
                user_id,
                _require_str(params, "operation_id"),
                lambda: design_voice(
                    user_id,
                    _require_str(params, "prompt"),
                    preview_text=_require_str(params, "preview_text"),
                    provider_name=_optional_str(params, "provider") or "",
                ),
            )
        except (ValueError, MissingLlmConfigError) as exc:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, str(exc)) from exc
        return result.model_dump()

    async def tts_generate_voice_prompt(params: dict) -> dict:
        try:
            result = await run_voice_operation(
                user_id,
                _require_str(params, "operation_id"),
                lambda: generate_voice_prompt(
                    user_id,
                    _optional_str(params, "current_prompt") or "",
                    _optional_str(params, "feedback") or "",
                    _optional_str(params, "preference") or "",
                ),
            )
        except (ValueError, MissingLlmConfigError) as exc:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, str(exc)) from exc
        return result.model_dump()

    async def tts_clone_voice(params: dict) -> dict:
        try:
            result = await run_voice_operation(
                user_id,
                _require_str(params, "operation_id"),
                lambda: clone_voice(
                    user_id,
                    _require_str(params, "sample_id"),
                    preview_text=_require_str(params, "preview_text"),
                ),
            )
        except (ValueError, MissingLlmConfigError) as exc:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, str(exc)) from exc
        return result.model_dump()

    async def tts_confirm_voice(params: dict) -> dict:
        version = params.get("expected_version")
        if type(version) is not int or version < 0:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "expected_version must be a nonnegative integer")
        try:
            return (await confirm_voice(user_id, _require_str(params, "candidate_id"), version)).model_dump()
        except (ValueError, MissingLlmConfigError) as exc:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, str(exc)) from exc

    async def tts_cancel_voice_operation(params: dict) -> dict:
        try:
            return {"cancelled": cancel_voice_operation(user_id, _require_str(params, "operation_id"))}
        except ValueError as exc:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, str(exc)) from exc

    register("tts.list_voices", tts_list_voices)

    register("tts.match_voice", tts_match_voice)

    # 制作通过账户内 operation_id 隔离，不排在普通 RPC 后面，确保取消能立即定位任务。
    register("tts.design_voice", tts_design_voice, concurrent=True)
    register("tts.generate_voice_prompt", tts_generate_voice_prompt, concurrent=True)
    register("tts.clone_voice", tts_clone_voice, concurrent=True)
    register("tts.confirm_voice", tts_confirm_voice)
    register("tts.cancel_voice_operation", tts_cancel_voice_operation, concurrent=True)
