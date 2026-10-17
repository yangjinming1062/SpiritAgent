"""媒体请求用例；业务校验与资产处理由所属模块执行。"""

import asyncio
from pathlib import Path

from infrastructure.config import SETTINGS
from infrastructure.constants import ATTACHMENT_VIDEO_EXTENSIONS, STT_MAX_AUDIO_BYTES
from infrastructure.logger import get_logger
from infrastructure.request_errors import RequestError
from infrastructure.uploads import UploadSource
from infrastructure.video_processing.audio import probe_audio
from infrastructure.video_processing.ffmpeg import VideoProcessError
from sqlalchemy.ext.asyncio import AsyncSession

from features.conversation.attachments import (
    attachment_audio_url,
    attachment_video_url,
    audio_mime_for_ext,
    enforce_session_quota,
    resolve_audio_file,
    resolve_video_file,
    save_audio_attachment,
    save_video_attachment,
    video_mime_for_ext,
)
from features.conversation.models import Conversation, parse_session_id
from features.conversation.speech_contracts import ChatAudioUploadResponse, ChatVideoUploadResponse
from features.media.http_contracts import MediaFile

logger = get_logger(__name__)


async def serve_session_video(session_id: str, file_id: str) -> MediaFile:
    """提供会话视频附件。公开（不鉴权）：file_id 为不可猜测 token，且公网模式下供应商需直接拉取。"""
    path = resolve_video_file(session_id, file_id)
    if path is None:
        raise RequestError(
            status_code=404,
            message="Video not found",
            code="not_found",
        )
    return MediaFile(path=path, media_type=video_mime_for_ext(path.suffix))


async def serve_session_audio(session_id: str, file_id: str, db: AsyncSession, user_id: int) -> MediaFile:
    if await Conversation.by_session_id(db, session_id, user_id=user_id) is None:
        raise RequestError(status_code=404, message="Audio not found")
    path = resolve_audio_file(session_id, file_id)
    if path is None:
        raise RequestError(status_code=404, message="Audio not found")
    await db.commit()
    return MediaFile(path=path, media_type=audio_mime_for_ext(path.suffix))


async def upload_chat_audio(
    db: AsyncSession,
    user_id: int,
    file: UploadSource,
    session_id: str,
) -> ChatAudioUploadResponse:
    raw_id = session_id.strip()
    conversation_id = parse_session_id(raw_id) if raw_id.isdecimal() and len(raw_id) <= 20 else None
    if conversation_id is None:
        raise RequestError(status_code=422, message="Invalid session_id")
    session_id = str(conversation_id)
    if await Conversation.by_session_id(db, session_id, user_id=user_id) is None:
        raise RequestError(status_code=404, message="Session not found")
    if file.size is None or file.size <= 0:
        raise RequestError(status_code=422, message="Empty audio")
    if file.size > min(STT_MAX_AUDIO_BYTES, SETTINGS.attachment_session_quota_bytes):
        raise RequestError(status_code=413, message="Audio exceeds upload or session byte limit")
    ext = Path(file.filename or "").suffix.lower()
    if ext not in {".wav", ".mp3", ".ogg", ".m4a", ".flac", ".webm"}:
        raise RequestError(status_code=415, message="Unsupported audio format")
    await db.commit()

    def save() -> tuple[str, float]:
        file_id, _ = save_audio_attachment(session_id, file.file, ext, STT_MAX_AUDIO_BYTES)
        path = resolve_audio_file(session_id, file_id)
        if path is None:
            raise ValueError("Audio file unavailable")
        try:
            duration = probe_audio(path)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return (file_id, duration)

    task = asyncio.create_task(asyncio.to_thread(save))
    try:
        file_id, duration = await asyncio.shield(task)
    except asyncio.CancelledError:
        # 请求关闭临时源文件之前，先等复制线程结束并释放未交付产物。
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not task.cancelled() and task.exception() is None:
            path = resolve_audio_file(session_id, task.result()[0])
            if path is not None:
                path.unlink(missing_ok=True)
        raise
    except (ValueError, VideoProcessError) as exc:
        raise RequestError(status_code=422, message="无法读取音频，请重新录制或上传有效音频") from exc
    try:
        await enforce_session_quota(db, session_id, 0)
        await db.commit()
    except BaseException:
        path = resolve_audio_file(session_id, file_id)
        if path is not None:
            path.unlink(missing_ok=True)
        raise
    return ChatAudioUploadResponse(
        url=attachment_audio_url(session_id, file_id),
        mime_type=audio_mime_for_ext(ext),
        duration=duration,
    )


async def upload_chat_video(
    db: AsyncSession,
    user_id: int,
    file: UploadSource | None = None,
    session_id: str = "",
) -> ChatVideoUploadResponse:
    """聊天视频附件上传：落会话目录（滚动配额）并返回附件 URL（本地相对 / 公网绝对）。"""
    if file is None:
        raise RequestError(
            status_code=422,
            message="Missing video file",
            code="missing_video_file",
        )
    raw_session_id = session_id.strip()
    conversation_id = (
        parse_session_id(raw_session_id) if raw_session_id.isdecimal() and len(raw_session_id) <= 20 else None
    )
    if conversation_id is None:
        raise RequestError(
            status_code=422,
            message="Invalid session_id",
            code="invalid_session_id",
        )
    session_id = str(conversation_id)
    if await Conversation.by_session_id(db, session_id, user_id=user_id) is None:
        raise RequestError(
            status_code=404,
            message="Session not found",
            code="not_found",
        )
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ATTACHMENT_VIDEO_EXTENSIONS:
        raise RequestError(
            status_code=415,
            message=f"Unsupported video format (allowed: {', '.join(sorted(ATTACHMENT_VIDEO_EXTENSIONS))})",
            code="unsupported_video_format",
        )
    max_bytes = SETTINGS.video_attachment_max_bytes
    hint = (
        ""
        if max_bytes == SETTINGS.attachment_session_quota_bytes
        else "；配置 server.public_base_url 后可经公网 URL 发送更大文件"
    )
    too_large = RequestError(
        status_code=413,
        message=f"Video too large (max {max_bytes // (1024 * 1024)} MB){hint}",
        code="payload_too_large",
    )
    size = file.size
    if size is None:
        raise RequestError(status_code=422, message="Video size unavailable")
    if size > max_bytes:
        raise too_large
    await db.commit()
    await enforce_session_quota(db, session_id, size)
    await db.commit()
    copy_task = asyncio.create_task(asyncio.to_thread(save_video_attachment, session_id, file.file, ext, max_bytes))
    try:
        file_id, size = await asyncio.shield(copy_task)
    except asyncio.CancelledError:
        # 重复取消也须等复制线程退出，避免关闭线程仍在读取的上传文件。
        while not copy_task.done():
            try:
                await asyncio.shield(copy_task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        try:
            result = copy_task.result()
        except (Exception, asyncio.CancelledError):
            result = None
        if isinstance(result, tuple):
            path = resolve_video_file(session_id, result[0])
            if path is not None:
                path.unlink(missing_ok=True)
        raise
    except ValueError as exc:
        raise too_large from exc
    logger.info("chat video uploaded", extra={"session_id": session_id, "file_id": file_id, "size": size})
    return ChatVideoUploadResponse(url=attachment_video_url(session_id, file_id))
