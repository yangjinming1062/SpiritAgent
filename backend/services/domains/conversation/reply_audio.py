import asyncio
import io
import math
from weakref import WeakValueDictionary

from components import get_logger, session_scope, utc_now
from modules.conversation import CompanionReply, Conversation, MediaBubble, Message, ReplyAudio, VoiceBubble
from modules.settings import resolve_user_timezone
from modules.ws import emit_ws_event
from mutagen import File as AudioFile
from sqlalchemy import select

from services.infrastructure.assets import (
    asset_store,
    client_asset_url,
    dated_asset_directory,
    save_companion_asset_async,
    user_asset_lock,
)
from services.infrastructure.llm import synthesize_speech

logger = get_logger(__name__)
_locks: WeakValueDictionary[tuple[int, int], asyncio.Lock] = WeakValueDictionary()
_tasks: dict[tuple[int, int], set[asyncio.Task[CompanionReply]]] = {}


def client_reply_bubbles(reply: CompanionReply) -> list[dict]:
    result: list[dict] = []
    for bubble in reply.bubbles:
        if isinstance(bubble, MediaBubble):
            result.append(
                {
                    "type": bubble.type,
                    "media_id": bubble.media_id,
                    "status": bubble.status,
                    "url": client_asset_url(bubble.url) if bubble.url else None,
                    "error": bubble.error,
                },
            )
            continue
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


async def _synthesize_bubble(user_id: int, bubble: VoiceBubble, deadline: float, directory: str) -> ReplyAudio:
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
        path = await save_companion_asset_async(
            audio.audio,
            user_id=user_id,
            label="chat-voice",
            ext=ext,
            directory=directory,
        )
        return ReplyAudio(url=path, duration=duration)


async def prepare_reply_audio(user_id: int, reply: CompanionReply) -> None:
    """为尚未接受的主动答复准备音频；调用方负责清理未提交的全部资产。"""
    async with session_scope() as db:
        timezone = await resolve_user_timezone(db, user_id) or "UTC"
    if reply.audio_directory is None:
        reply.audio_directory = dated_asset_directory(utc_now(), timezone=timezone)
    directory = reply.audio_directory
    deadline = asyncio.get_running_loop().time() + 120
    for index, bubble in enumerate(reply.bubbles):
        if isinstance(bubble, VoiceBubble) and bubble.audio is None:
            try:
                bubble.audio = await _synthesize_bubble(user_id, bubble, deadline, directory)
            except Exception:
                logger.warning("Proactive voice synthesis failed", extra={"bubble_index": index}, exc_info=True)


async def discard_reply_audio(reply: CompanionReply) -> None:
    for bubble in reply.bubbles:
        if isinstance(bubble, VoiceBubble) and bubble.audio:
            await asyncio.to_thread(asset_store.unlink_companion_asset, bubble.audio.url)
            bubble.audio = None


async def _synthesize_reply_audio(
    user_id: int,
    message_id: int,
    *,
    bubble_index: int | None = None,
) -> CompanionReply:
    """按消息串行合成；短事务核对语音语义并合并最新媒体状态，防止迟到覆盖。"""
    key = (user_id, message_id)
    lock = _locks.setdefault(key, asyncio.Lock())
    async with asyncio.timeout(30):
        await lock.acquire()
    try:
        async with user_asset_lock(user_id), session_scope() as db:
            row = await db.scalar(
                select(Message)
                .join(Conversation)
                .where(
                    Message.id == message_id,
                    Conversation.user_id == user_id,
                )
                .with_for_update(of=Message),
            )
            if row is None or not row.reply_json:
                raise LookupError("Reply not found")
            original_content = row.content
            session_id = str(row.conversation_id)
            reply = CompanionReply.model_validate_json(row.reply_json)
            reply.validate_content(row.content or "")
            if reply.audio_directory is None:
                reply.audio_directory = dated_asset_directory(
                    row.created_at,
                    timezone=await resolve_user_timezone(db, user_id) or "UTC",
                )
                row.reply_json = reply.model_dump_json()
                await db.commit()
            directory = reply.audio_directory
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
                bubble.audio = await _synthesize_bubble(user_id, bubble, deadline, directory)
                path = bubble.audio.url
                async with user_asset_lock(user_id), session_scope() as db:
                    current = await db.scalar(
                        select(Message)
                        .where(
                            Message.id == message_id,
                            Message.conversation.has(Conversation.user_id == user_id),
                        )
                        .with_for_update(),
                    )
                    if current is None or current.content != original_content or current.reply_json is None:
                        raise LookupError("Reply changed during synthesis")
                    latest = CompanionReply.model_validate_json(current.reply_json)
                    if index >= len(latest.bubbles) or latest.bubbles[index].model_dump(
                        exclude={"audio"},
                    ) != bubble.model_dump(exclude={"audio"}):
                        raise LookupError("Voice bubble changed during synthesis")
                    latest.bubbles[index] = bubble
                    reply = latest
                    current.reply_json = reply.model_dump_json()
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


async def synthesize_reply_audio(
    user_id: int,
    message_id: int,
    *,
    bubble_index: int | None = None,
) -> CompanionReply:
    """每次合成有独立、按消息归属的任务；删除消息只取消对应音频。"""
    key = (user_id, message_id)
    task = asyncio.create_task(
        _synthesize_reply_audio(user_id, message_id, bubble_index=bubble_index),
        name=f"reply-audio:{user_id}:{message_id}",
    )
    owned = _tasks.setdefault(key, set())
    owned.add(task)
    try:
        return await task
    finally:
        owned.discard(task)
        if not owned:
            _tasks.pop(key, None)


async def cancel_reply_audio(user_id: int, message_ids: set[int]) -> None:
    tasks = {task for message_id in message_ids for task in _tasks.get((user_id, message_id), ())}
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
