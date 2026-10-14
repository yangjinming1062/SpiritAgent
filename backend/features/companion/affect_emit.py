from infrastructure.database import SESSION_LOCAL

from features.companion.proactive_runtime import PresentationSnapshot, get_presentation_snapshot, note_outreach_throttle
from features.conversation.proactive_messages import append_companion_message
from features.conversation.replies import CompanionReply, CompanionReplyInput, TextBubble


async def emit_companion_message(
    user_id: int,
    text: str,
    *,
    presentation: PresentationSnapshot | None = None,
) -> bool:
    """返回是否保存并投递主动消息；自主搭话在提交前复核呈现快照，展示与静止档门控仍由各自调用方负责。"""
    clean_text = text.strip()
    if not clean_text:
        return False
    bubble = TextBubble(type="text", text=clean_text)
    content = CompanionReplyInput(root=[bubble]).model_dump_json()
    async with SESSION_LOCAL() as db:
        await append_companion_message(db, user_id, CompanionReply(bubbles=[bubble]), content)
        if presentation is not None and presentation != get_presentation_snapshot(user_id):
            return False
        await db.commit()
    note_outreach_throttle(user_id)
    return True
