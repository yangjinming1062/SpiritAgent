"""会话消息前向重建模块。

按 Message.id 升序遍历，保证 assistant 的 tool_calls 早于同轮 tool 结果，
从而预先填充 call_id -> name 映射供后续 tool 消息回填 tool_name。
"""

from components import safe_json_loads
from modules.conversation import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def build_session_messages(
    conv_id: int,
    db: AsyncSession,
    *,
    after_id: int | None = None,
    before_id: int | None = None,
    limit: int | None = None,
    desc: bool = False,
    include_id: bool = False,
) -> list[dict]:
    """前向重建会话消息列表。desc 与 after_id/before_id 互斥约束保证分页单向性。"""
    if desc and after_id is not None:
        raise ValueError("desc=True and after_id are mutually exclusive")
    if after_id is not None and before_id is not None:
        raise ValueError("after_id and before_id are mutually exclusive")
    stmt = select(Message).where(Message.conversation_id == conv_id).order_by(Message.id.desc() if desc else Message.id)
    if after_id is not None:
        stmt = stmt.where(Message.id > after_id)
    if before_id is not None:
        stmt = stmt.where(Message.id < before_id)
    if limit is not None:
        stmt = stmt.limit(limit)
    messages = (await db.execute(stmt)).scalars().all()

    # 若按降序拉取最新消息，先翻转为升序执行前向重建，确保 tool_calls 早于 tool 结果被处理
    if desc:
        messages = list(reversed(messages))

    tool_name_by_call_id: dict[str, str] = {}
    result: list[dict] = []
    for msg in messages:
        item: dict = {"role": msg.role, "content": msg.content}
        if msg.subtype:
            item["subtype"] = msg.subtype
        if msg.media_json:
            media = safe_json_loads(msg.media_json, default=None)
            if isinstance(media, list) and media:
                item["media"] = [
                    {
                        **entry,
                        **{
                            key: "/api/companion/asset/" + entry[key].removeprefix("companion-assets/")
                            for key in ("url", "audio_url")
                            if isinstance(entry.get(key), str) and entry[key].startswith("companion-assets/")
                        },
                    }
                    for entry in media
                    if isinstance(entry, dict)
                ]
        if msg.speech_style_json:
            item["speech_style"] = safe_json_loads(msg.speech_style_json, default=None)
        if msg.reasoning_content:
            item["reasoning"] = msg.reasoning_content
        if include_id:
            item["id"] = msg.id
        if msg.created_at is not None:
            item["timestamp"] = int(msg.created_at.timestamp() * 1000)

        if msg.tool_calls:
            calls = safe_json_loads(msg.tool_calls, default=None)
            if isinstance(calls, list):
                item["tool_calls"] = calls
                if msg.role == "assistant":
                    for call in calls:
                        if not isinstance(call, dict):
                            continue
                        call_id = call.get("call_id")
                        name = call.get("name")
                        if isinstance(call_id, str) and isinstance(name, str) and name:
                            tool_name_by_call_id[call_id] = name
            else:
                item["tool_calls"] = msg.tool_calls

        if msg.tool_call_id:
            item["tool_call_id"] = msg.tool_call_id
        if msg.role == "tool" and msg.tool_call_id:
            item["tool_name"] = tool_name_by_call_id.get(msg.tool_call_id, "")

        result.append(item)

    if desc:
        result.reverse()
    return result
