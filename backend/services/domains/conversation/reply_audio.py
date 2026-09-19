import asyncio
import io
import math
from weakref import WeakValueDictionary

from components import get_logger, session_scope
from modules.conversation import CompanionReply, Conversation, Message, ReplyAudio, VoiceBubble
from modules.ws import emit_ws_event
from mutagen import File as AudioFile
from sqlalchemy import select, update

from services.infrastructure.assets import asset_store, client_asset_url, save_companion_asset_async
from services.infrastructure.llm import synthesize_speech

logger = get_logger(__name__)
_locks: WeakValueDictionary[tuple[int, int], asyncio.Lock] = WeakValueDictionary()


def client_reply_bubbles(reply: CompanionReply) -> list[dict]:
    result: list[dict] = []
    for bubble in reply.bubbles:
        item: dict = {"type": bubble.type, "text": bubble.text}
        if bubble.type == "voice":
            item["audio"] = (
                {"url": client_asset_url(bubble.audio.url), "duration": bubble.audio.duration} if bubble.audio else None
            )
        result.append(item)
    return result


def _audio_info(data: bytes) -> tuple[float, str]:
    audio = AudioFile(io.BytesIO(data))
    if audio is None or not math.isfinite(audio.info.length) or audio.info.length <= 0:
        raise ValueError("TTS returned invalid audio")
    mime = audio.mime[0]
    extension = {
        "audio/mp3": "mp3",
        "audio/mpeg": "mp3",
        "audio/wav": "wav",
        "audio/ogg": "ogg",
        "audio/flac": "flac",
    }.get(mime)
    if extension is None:
        raise ValueError("TTS returned unsupported audio")
    return float(audio.info.length), extension


async def _synthesize_bubble(user_id: int, bubble: VoiceBubble, deadline: float) -> ReplyAudio:
    async with asyncio.timeout(max(0, min(60, deadline - asyncio.get_running_loop().time()))):
        audio = await synthesize_speech(
            user_id,
            bubble.text,
            bubble.voice_id,
            bubble.language,
            bubble.speech,
            preserve_performance=True,
        )
        duration, ext = await asyncio.to_thread(_audio_info, audio.audio)
        path = await save_companion_asset_async(audio.audio, user_id=user_id, label="chat-voice", ext=ext)
        return ReplyAudio(url=path, duration=duration)


async def prepare_reply_audio(user_id: int, reply: CompanionReply) -> None:
    """为尚未接受的主动答复准备音频；调用方负责清理未提交的全部资产。"""
    deadline = asyncio.get_running_loop().time() + 120
    for index, bubble in enumerate(reply.bubbles):
        if isinstance(bubble, VoiceBubble) and bubble.audio is None:
            try:
                bubble.audio = await _synthesize_bubble(user_id, bubble, deadline)
            except Exception:
                logger.warning("Proactive voice synthesis failed", extra={"bubble_index": index}, exc_info=True)


async def discard_reply_audio(reply: CompanionReply) -> None:
    for bubble in reply.bubbles:
        if isinstance(bubble, VoiceBubble) and bubble.audio:
            await asyncio.to_thread(asset_store.unlink_companion_asset, bubble.audio.url)
            bubble.audio = None


async def synthesize_reply_audio(
    user_id: int,
    message_id: int,
    *,
    bubble_index: int | None = None,
) -> CompanionReply:
    """按消息串行合成；成功音频幂等复用，短事务 CAS 防止删除或恢复后的迟到覆盖。"""
    key = (user_id, message_id)
    lock = _locks.setdefault(key, asyncio.Lock())
    async with asyncio.timeout(30):
        await lock.acquire()
    try:
        async with session_scope() as db:
            row = await db.scalar(
                select(Message)
                .join(Conversation)
                .where(
                    Message.id == message_id,
                    Conversation.user_id == user_id,
                ),
            )
            if row is None or not row.reply_json:
                raise LookupError("Reply not found")
            original = row.reply_json
            session_id = str(row.conversation_id)
            reply = CompanionReply.model_validate_json(original)
        if bubble_index is not None and (
            not 0 <= bubble_index < len(reply.bubbles) or reply.bubbles[bubble_index].type != "voice"
        ):
            raise LookupError("Voice bubble not found")
        deadline = asyncio.get_running_loop().time() + 120
        for index, bubble in enumerate(reply.bubbles):
            if (
                not isinstance(bubble, VoiceBubble)
                or bubble.audio
                or bubble_index is not None
                and index != bubble_index
            ):
                continue
            path: str | None = None
            committed = False
            try:
                bubble.audio = await _synthesize_bubble(user_id, bubble, deadline)
                path = bubble.audio.url
                updated = reply.model_dump_json()
                async with session_scope() as db:
                    changed = await db.scalar(
                        update(Message)
                        .where(
                            Message.id == message_id,
                            Message.reply_json == original,
                            Message.conversation.has(Conversation.user_id == user_id),
                        )
                        .values(reply_json=updated)
                        .returning(Message.id),
                    )
                    if changed is None:
                        raise LookupError("Reply changed during synthesis")
                    emit_ws_event(
                        db,
                        user_id=user_id,
                        event_type="message.voice",
                        payload={
                            "session_id": session_id,
                            "message_id": message_id,
                            "bubble_index": index,
                            "bubble": client_reply_bubbles(reply)[index],
                        },
                    )
                    commit_task = asyncio.create_task(db.commit())
                    try:
                        await asyncio.shield(commit_task)
                    except asyncio.CancelledError:
                        await commit_task
                        committed = True
                        raise
                    committed = True
                    original = updated
            except LookupError:
                raise
            except Exception:
                if not committed:
                    bubble.audio = None
                logger.warning(
                    "Voice bubble synthesis failed",
                    extra={"message_id": message_id, "bubble_index": index},
                    exc_info=True,
                )
            finally:
                if path and not committed:
                    await asyncio.to_thread(asset_store.unlink_companion_asset, path)
        return reply
    finally:
        lock.release()
