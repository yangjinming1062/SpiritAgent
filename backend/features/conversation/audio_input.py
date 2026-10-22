import asyncio
import base64
import json
from typing import Any
from weakref import WeakValueDictionary

from infrastructure.ai.stt_service import transcribe_audio
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from infrastructure.text import safe_json_loads
from infrastructure.video_processing.audio import audio_wav_bytes
from runtime.events.emit import emit_ws_event
from sqlalchemy import select

from features.conversation.attachments import audio_file_id_from_url, resolve_audio_file
from features.conversation.models import Conversation, Message

_TRANSCRIPTION_LOCKS: WeakValueDictionary[tuple[str, str], asyncio.Lock] = WeakValueDictionary()
logger = get_logger(__name__)


async def _saved_transcript(user_id: int, session_id: str, url: str) -> str | None:
    async with SESSION_LOCAL() as db:
        if await Conversation.by_session_id(db, session_id, user_id=user_id) is None:
            raise ValueError("音频不属于当前会话")
        for content in await db.scalars(
            select(Message.content).where(
                Message.conversation_id == int(session_id),
                Message.content_type == "multimodal_v1",
                Message.content.contains(url),
            ),
        ):
            for part in safe_json_loads(content or "[]", default=[]):
                if (
                    isinstance(part, dict)
                    and part.get("type") == "input_audio"
                    and part.get("audio_url") == url
                    and isinstance(part.get("transcript"), str)
                    and part["transcript"].strip()
                ):
                    return part["transcript"]
    return None


async def _save_audio_details(
    user_id: int,
    session_id: str,
    url: str,
    *,
    duration: float | None = None,
    transcript: str | None = None,
) -> None:
    async with SESSION_LOCAL() as db:
        if await Conversation.by_session_id(db, session_id, user_id=user_id) is None:
            return
        rows = await db.scalars(
            select(Message)
            .where(
                Message.conversation_id == int(session_id),
                Message.content_type == "multimodal_v1",
                Message.content.contains(url),
            )
            .with_for_update(),
        )
        for row in rows:
            parts = safe_json_loads(row.content or "[]", default=[])
            changed = False
            for part in parts:
                if isinstance(part, dict) and part.get("type") == "input_audio" and part.get("audio_url") == url:
                    if duration is not None and part.get("duration") != duration:
                        part["duration"] = duration
                        changed = True
                    if transcript is not None and part.get("transcript") != transcript:
                        part["transcript"] = transcript
                        changed = True
            if changed:
                row.content = json.dumps(parts, ensure_ascii=False)
                emit_ws_event(
                    db,
                    user_id=user_id,
                    event_type="message.audio",
                    payload={
                        "session_id": session_id,
                        "message_id": row.id,
                        "audio_url": url,
                        **({"duration": duration} if duration is not None else {}),
                        **({"transcript": transcript} if transcript is not None else {}),
                    },
                )
        await db.commit()


async def _transcript(
    user_id: int,
    session_id: str,
    url: str,
    *,
    transcripts: dict[str, str | Exception] | None = None,
) -> str:
    key = (session_id, url)
    lock = _TRANSCRIPTION_LOCKS.setdefault(key, asyncio.Lock())
    async with lock:
        cached = transcripts.get(url) if transcripts is not None else None
        if isinstance(cached, Exception):
            raise cached
        if cached is not None:
            return cached
        try:
            text = await _saved_transcript(user_id, session_id, url)
            if not text:
                file_id = audio_file_id_from_url(url, session_id)
                path = resolve_audio_file(session_id, file_id) if file_id else None
                if path is None:
                    raise ValueError("音频附件已失效")
                audio, duration = await asyncio.to_thread(audio_wav_bytes, path)
                text = (await transcribe_audio(user_id, audio, "audio/wav")).strip()
                if not text:
                    raise ValueError("未识别到音频中的文字")
                await _save_audio_details(user_id, session_id, url, duration=duration, transcript=text)
        except Exception as exc:
            if transcripts is not None:
                transcripts[url] = exc
            raise
        if transcripts is not None:
            transcripts[url] = text
        return text


async def prepare_audio_input(
    user_id: int,
    session_id: str,
    items: list[dict[str, Any]],
    *,
    native: bool,
    transcripts: dict[str, str | Exception] | None = None,
) -> list[dict[str, Any]]:
    """只改请求副本；原始音频地址与转写保留在会话消息中。"""
    result = []
    for item in items:
        key = "output" if item.get("type") == "function_call_output" else "content"
        content = item.get(key)
        if not isinstance(content, list):
            result.append(item)
            continue
        parts = []
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "input_audio":
                parts.append(part)
                continue
            url = str(part.get("audio_url") or "")
            if not native:
                text = await _transcript(user_id, session_id, url, transcripts=transcripts)
                parts.append({"type": "input_text", "text": text})
                continue
            file_id = audio_file_id_from_url(url, session_id)
            path = resolve_audio_file(session_id, file_id) if file_id else None
            if path is None:
                raise ValueError("音频附件已失效")
            audio, duration = await asyncio.to_thread(audio_wav_bytes, path)
            await _save_audio_details(user_id, session_id, url, duration=duration)
            parts.append(
                {
                    "type": "input_audio",
                    "format": "wav",
                    "data": "data:audio/wav;base64," + base64.b64encode(audio).decode("ascii"),
                    "duration": duration,
                },
            )
        result.append({**item, key: parts})
    return result


async def ensure_audio_transcripts(
    user_id: int,
    session_id: str,
    *,
    transcripts: dict[str, str | Exception] | None = None,
) -> None:
    async with SESSION_LOCAL() as db:
        if await Conversation.by_session_id(db, session_id, user_id=user_id) is None:
            return
        contents = list(
            await db.scalars(
                select(Message.content).where(
                    Message.conversation_id == int(session_id),
                    Message.content_type == "multimodal_v1",
                    Message.content.contains('"input_audio"'),
                ),
            ),
        )
    for content in contents:
        parts = safe_json_loads(content or "[]", default=[])
        for part in parts if isinstance(parts, list) else []:
            if not isinstance(part, dict) or part.get("type") != "input_audio" or part.get("transcript"):
                continue
            try:
                await _transcript(user_id, session_id, str(part.get("audio_url") or ""), transcripts=transcripts)
            except Exception as exc:
                logger.warning("Auxiliary audio transcription unavailable", extra={"error_type": type(exc).__name__})
