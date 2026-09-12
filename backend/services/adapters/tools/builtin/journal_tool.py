"""moment_create / diary_write 工具：角色主动记录生活空间时刻与日记。

门控：
- 静止档禁止主动调用
- moment_create 主动配额：每用户每天 3
- 用户回合的「记下来」不算主动配额
- 工作预设会话不刷生活时刻（开发计划 §7.4：developer / product_manager / copywriter / language_teacher）
"""

import json
from datetime import date
from typing import Any

from components import SESSION_LOCAL, get_logger, tool_error
from modules.companion import DiarySource, MomentKind, MomentSource

from services.domains.companion import is_work_preset, resolve_session_preset
from services.domains.companion.disturbance import get_disturbance_tier
from services.domains.journal import check_moment_llm_quota, create_user_moment, resolve_user_local_today, upsert_diary
from services.infrastructure.tool_runtime.registry import REGISTRY

logger = get_logger(__name__)

_VALID_MOMENT_KINDS: frozenset[str] = frozenset(k.value for k in MomentKind)


async def moment_create_tool(
    title: str,
    body: str,
    emotion: str | None = None,
    kind: str = "user",
    user_id: int | None = None,
    disturbance_tier: str | None = None,
    parent_session_id: str | None = None,
    **kwargs: Any,
) -> str:
    if user_id is None:
        return tool_error("写时刻需要用户上下文")
    clean_title = (title or "").strip()
    clean_body = (body or "").strip()
    if not clean_title or not clean_body:
        return tool_error("时刻标题和内容不能为空")
    tier = disturbance_tier or (kwargs.get("user_settings") or {}).get("companion.disturbance_tier")
    if tier is None and user_id is not None:
        tier = await get_disturbance_tier(user_id)
    tier = (tier or "normal").lower()
    if tier in ("still", "silent"):
        return tool_error("先把这事放下吧，等你想说的时候再说。")
    if kind not in _VALID_MOMENT_KINDS:
        kind = MomentKind.USER.value
    session_id_int: int | None = None
    if parent_session_id is not None:
        try:
            session_id_int = int(parent_session_id)
        except (ValueError, TypeError):
            session_id_int = None
    # 工作预设会话不刷生活时刻：与工作预设（developer/pm/copywriter/language_teacher）走自己的 LLM 上下文
    async with SESSION_LOCAL() as db:
        preset = await resolve_session_preset(db, parent_session_id)
        if is_work_preset(preset):
            logger.info(
                "moment_create skipped: work preset session",
                extra={"user_id": user_id, "session_id": parent_session_id, "preset": preset},
            )
            return tool_error("这是工作对话，不写生活时刻。")
        if not await check_moment_llm_quota(db, user_id):
            return tool_error("今天记下的时刻已经够多了，明天再记录吧。")
        row = await create_user_moment(
            db,
            user_id,
            title=clean_title,
            body=clean_body,
            emotion=emotion,
            kind=kind,
            source=MomentSource.LLM.value,
            session_id=session_id_int,
        )
    return json.dumps({"success": True, "moment_id": row.id}, ensure_ascii=False)


async def diary_write_tool(
    body: str,
    mood: str | None = None,
    date_str: str | None = None,
    title: str | None = None,
    user_id: int | None = None,
    disturbance_tier: str | None = None,
    parent_session_id: str | None = None,
    **kwargs: Any,
) -> str:
    if user_id is None:
        return tool_error("写日记需要用户上下文")
    clean_body = (body or "").strip()
    if not clean_body:
        return tool_error("日记内容不能为空")
    tier = disturbance_tier or (kwargs.get("user_settings") or {}).get("companion.disturbance_tier")
    if tier is None and user_id is not None:
        tier = await get_disturbance_tier(user_id)
    tier = (tier or "normal").lower()
    if tier in ("still", "silent"):
        return tool_error("现在不想动笔，等你想聊的时候再说。")
    raw_date = date_str or kwargs.get("date")
    target_date: date | None = None
    if raw_date:
        try:
            target_date = date.fromisoformat(raw_date)
        except ValueError:
            return tool_error(f"无效的日期格式 '{raw_date}'，必须为 YYYY-MM-DD")

    async with SESSION_LOCAL() as db:
        preset = await resolve_session_preset(db, parent_session_id)
        if is_work_preset(preset):
            logger.info(
                "diary_write skipped: work preset session",
                extra={"user_id": user_id, "session_id": parent_session_id, "preset": preset},
            )
            return tool_error("这是工作对话，不写日记。")

        entry_date = target_date or await resolve_user_local_today(db, user_id)
        row = await upsert_diary(
            db,
            user_id,
            entry_date=entry_date,
            title=(title or "").strip(),
            body=clean_body,
            mood=mood,
            source=DiarySource.LLM.value,
            edited_at=None,
        )
    return json.dumps({"success": True, "diary_id": row.id, "entry_date": entry_date.isoformat()}, ensure_ascii=False)


MOMENT_CREATE_SCHEMA = {
    "name": "moment_create",
    "description": "在用户生活空间时间线写一条时刻。系统已对主动配额（每用户每天 3）做节流；静止档与工作预设会话不调用。",
    "parameters": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "短标题（≤ 24 字）"},
            "body": {"type": "string", "description": "80–240 字的正文，第一人称或第二人称皆可"},
            "emotion": {"type": "string", "description": "可选情绪 token（白名单见 emotion 枚举）"},
            "kind": {
                "type": "string",
                "enum": ["greeting", "emotion", "together", "milestone", "scene", "user"],
                "description": "默认 user",
            },
        },
        "required": ["title", "body"],
    },
}

DIARY_WRITE_SCHEMA = {
    "name": "diary_write",
    "description": "在用户日记本追加一段（用户时区今天），第一人称；不覆盖用户已写过的当日内容（按追加段落处理）。静止档与工作预设会话不调用。",
    "parameters": {
        "type": "object",
        "properties": {
            "body": {"type": "string", "description": "日记正文（≤ 1000 字）"},
            "mood": {"type": "string", "description": "可选情绪 token"},
            "date": {"type": "string", "description": "ISO 日期（YYYY-MM-DD）；缺省为用户本地今天"},
            "title": {"type": "string", "description": "可选标题"},
        },
        "required": ["body"],
    },
}


def register(registry) -> None:
    REGISTRY.register("moment_create", MOMENT_CREATE_SCHEMA, moment_create_tool)
    REGISTRY.register("diary_write", DIARY_WRITE_SCHEMA, diary_write_tool)
