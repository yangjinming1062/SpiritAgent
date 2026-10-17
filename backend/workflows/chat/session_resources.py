"""HTTP 会话资源与账户运行态的同步边界。"""

from dataclasses import asdict

from features.configuration.settings.values import load_user_settings
from features.conversation.ai_routing import conversation_ai_task
from features.conversation.schemas import DesktopSessionPatchRequest
from features.conversation.session_management import patch_session
from features.conversation.session_operations import get_conversation
from features.conversation.snapshots import ConversationSnapshot
from infrastructure.ai.user_config import resolve_user_llm_config
from infrastructure.database import SESSION_LOCAL
from runtime.chat_accounts import get_account_runtime
from runtime.conversation import SessionCreateResult, build_runtime_info, decode_session_settings, new_runtime_session
from sqlalchemy.ext.asyncio import AsyncSession

from workflows.chat.turn_inputs import merge_session_settings, resolve_inference_settings


async def session_resource(user_id: int, conv: ConversationSnapshot) -> SessionCreateResult:
    account = get_account_runtime(user_id)
    runtime = account.runtime_sessions.get(str(conv.id)) if account else None
    if runtime is None:
        runtime = new_runtime_session(
            conversation_id=conv.id,
            settings_json=conv.settings_json,
            kind=conv.kind,
            is_automation=conv.is_automation,
        )
    async with SESSION_LOCAL() as db:
        user_settings = await load_user_settings(db, user_id)
        llm_config = await resolve_user_llm_config(db, user_id, conversation_ai_task(conv))
    effective = merge_session_settings(user_settings, runtime.settings, conv=conv)
    settings = runtime.settings | asdict(resolve_inference_settings(effective, conv=conv))
    return SessionCreateResult(
        session_id=str(conv.id),
        info=build_runtime_info(llm_config, runtime, settings, system_preset_id=conv.system_preset_id),
    )


async def patch_session_resource(
    user_id: int,
    db: AsyncSession,
    session_id: str,
    patch: DesktopSessionPatchRequest,
) -> SessionCreateResult:
    account = get_account_runtime(user_id)
    runtime = account.runtime_sessions.get(session_id) if account else None
    if runtime is not None:
        async with runtime.snapshot_lock:
            conv = await patch_session(user_id, db, session_id, patch)
            runtime.settings = decode_session_settings(conv.settings_json)
    else:
        conv = await patch_session(user_id, db, session_id, patch)
        account = get_account_runtime(user_id)
        mounted = account.runtime_sessions.get(str(conv.id)) if account else None
        if mounted is not None:
            # HTTP 写入期间可能由另一设备首次挂载；锁内重读，避免旧响应覆盖较新的设置。
            async with mounted.snapshot_lock:
                conv = await get_conversation(user_id, str(conv.id))
                mounted.settings = decode_session_settings(conv.settings_json)
    return await session_resource(user_id, conv)
