"""伙伴日记补记工具。"""

import datetime
import json

from components import SESSION_LOCAL, tool_error
from modules.companion import DiarySource
from prompts.tools import (
    DIARY_WRITE_DESC,
    DIARY_WRITE_PARAM_DESCS,
)

from services.domains.journal import resolve_user_local_today, upsert_diary
from services.infrastructure.tool_runtime import ToolsRegistry


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
    target_date: datetime.date | None = None
    if date:
        try:
            target_date = datetime.date.fromisoformat(date)
        except ValueError:
            return tool_error(f"无效的日期格式 '{date}'，必须为 YYYY-MM-DD")

    async with SESSION_LOCAL() as db:
        today = await resolve_user_local_today(db, user_id)
        if target_date is not None and target_date > today:
            return tool_error("日记只记录已经发生的日子，不能写未来日期；本次未保存")
        entry_date = target_date or today
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
    registry.register(DIARY_WRITE_SCHEMA, diary_write_tool)
