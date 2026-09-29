"""moment_create / diary_write 工具：角色主动记录生活空间时刻与日记。

门控：
- 静止档禁止主动调用
- moment_create 每日配额见 `moment_llm_per_day`
- 工作预设会话不绑定这两个工具（回合装配层过滤，见 prompt_presets.LIFE_SPACE_TOOL_NAMES）
"""

import datetime
import json

from components import SESSION_LOCAL, tool_error
from modules.companion import DiarySource, MomentKind, MomentSource
from prompts.tools import (
    DIARY_WRITE_DESC,
    DIARY_WRITE_PARAM_DESCS,
    MOMENT_CREATE_DESC,
    MOMENT_CREATE_PARAM_DESCS,
)

from services.domains.companion import is_still
from services.domains.journal import check_moment_llm_quota, create_user_moment, resolve_user_local_today, upsert_diary
from services.infrastructure.tool_runtime import ToolsRegistry

_VALID_MOMENT_KINDS: frozenset[str] = frozenset(k.value for k in MomentKind)


async def moment_create_tool(
    title: str,
    body: str,
    user_id: int,
    parent_session_id: str,
    emotion: str | None = None,
    kind: str = MomentKind.EMOTION.value,
    **_: object,
) -> str:
    clean_title = (title or "").strip()
    clean_body = (body or "").strip()
    if not clean_title or not clean_body:
        return tool_error("时刻标题和内容不能为空")
    if len(clean_title) > 24 or len(clean_body) > 500:
        return tool_error("片刻标题最多 24 字符，正文最多 500 字符；请精简后提交，内容尚未保存")
    if await is_still(user_id):
        return tool_error("先把这事放下吧，等你想说的时候再说。")
    if kind not in _VALID_MOMENT_KINDS:
        kind = MomentKind.EMOTION.value
    async with SESSION_LOCAL() as db:
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
            session_id=int(parent_session_id),
        )
    return json.dumps({"success": True, "moment_id": row.id}, ensure_ascii=False)


async def diary_write_tool(
    body: str,
    user_id: int,
    mood: str | None = None,
    date: str | None = None,
    title: str | None = None,
    **_: object,
) -> str:
    clean_body = (body or "").strip()
    if not clean_body:
        return tool_error("日记内容不能为空")
    if len(clean_body) > 1000:
        return tool_error("本次日记补记最多 1000 字符，请精简后提交；内容尚未保存")
    if await is_still(user_id):
        return tool_error("现在不想动笔，等你想聊的时候再说。")
    target_date: datetime.date | None = None
    if date:
        try:
            target_date = datetime.date.fromisoformat(date)
        except ValueError:
            return tool_error(f"无效的日期格式 '{date}'，必须为 YYYY-MM-DD")

    async with SESSION_LOCAL() as db:
        entry_date = target_date or await resolve_user_local_today(db, user_id)
        try:
            row = await upsert_diary(
                db,
                user_id,
                entry_date=entry_date,
                title=(title or "").strip(),
                body=clean_body,
                mood=mood,
                source=DiarySource.LLM.value,
            )
        except ValueError as exc:
            return tool_error(str(exc))
    return json.dumps({"success": True, "diary_id": row.id, "entry_date": entry_date.isoformat()}, ensure_ascii=False)


MOMENT_CREATE_SCHEMA = {
    "name": "moment_create",
    "description": MOMENT_CREATE_DESC,
    "parameters": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "maxLength": 24, "description": MOMENT_CREATE_PARAM_DESCS["title"]},
            "body": {"type": "string", "maxLength": 500, "description": MOMENT_CREATE_PARAM_DESCS["body"]},
            "emotion": {"type": "string", "description": MOMENT_CREATE_PARAM_DESCS["emotion"]},
            "kind": {
                "type": "string",
                "enum": ["emotion", "together", "scene"],
                "description": MOMENT_CREATE_PARAM_DESCS["kind"],
            },
        },
        "required": ["title", "body"],
    },
}

DIARY_WRITE_SCHEMA = {
    "name": "diary_write",
    "description": DIARY_WRITE_DESC,
    "parameters": {
        "type": "object",
        "properties": {
            "body": {"type": "string", "maxLength": 1000, "description": DIARY_WRITE_PARAM_DESCS["body"]},
            "mood": {"type": "string", "description": DIARY_WRITE_PARAM_DESCS["mood"]},
            "date": {"type": "string", "description": DIARY_WRITE_PARAM_DESCS["date"]},
            "title": {"type": "string", "description": DIARY_WRITE_PARAM_DESCS["title"]},
        },
        "required": ["body"],
    },
}


def register(registry: ToolsRegistry) -> None:
    registry.register(MOMENT_CREATE_SCHEMA, moment_create_tool)
    registry.register(DIARY_WRITE_SCHEMA, diary_write_tool)
