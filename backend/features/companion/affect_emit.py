from infrastructure.database import SESSION_LOCAL

from features.companion.proactive_runtime import note_outreach_throttle
from features.conversation.proactive_messages import append_companion_message
from features.conversation.replies import CompanionReply, CompanionReplyInput, TextBubble


async def emit_companion_message(user_id: int, text: str) -> bool:
    """保存并投递非空主动消息；静止档门控由调用方负责。"""
    clean_text = text.strip()
    if not clean_text:
        return False
    bubble = TextBubble(type="text", text=clean_text)
    content = CompanionReplyInput(root=[bubble]).model_dump_json()
    async with SESSION_LOCAL() as db:
        await append_companion_message(db, user_id, CompanionReply(bubbles=[bubble]), content)
        await db.commit()
    note_outreach_throttle(user_id)
    return True
