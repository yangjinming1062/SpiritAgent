import asyncio
from pathlib import Path
from urllib.parse import quote

from common import get_router
from components import (
    ATTACHMENT_VIDEO_EXTENSIONS,
    ATTACHMENT_VIDEO_MAX_BYTES,
    SETTINGS,
    STT_MAX_AUDIO_BYTES,
    TTS_MAX_TEXT_CHARS,
    DbSession,
    get_file_path,
    get_logger,
)
from fastapi import File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from modules.auth import CurrentUser
from modules.conversation import Conversation
from modules.media import ChatVideoUploadResponse, SpeechToTextResponse
from pydantic import BaseModel
from services.adapters.http import limiter
from services.domains.media import (
    attachment_video_url,
    enforce_session_quota,
    resolve_video_file,
    save_video_attachment,
    video_mime_for_ext,
)
from services.infrastructure.llm import MissingLlmConfigError, synthesize_speech, transcribe_audio

from ._http_errors import llm_http_error, missing_config_http

logger = get_logger(__name__)


router = get_router()


def _resolve_mime_type(content_type: str | None) -> str:
    """把 content_type 归一为 ASR 支持的 MIME 类型。"""
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct in ("audio/mp3", "audio/mpeg"):
        return "audio/mpeg"
    if ct in ("audio/webm", "video/webm"):
        return "audio/webm"
    if ct in ("audio/ogg", "audio/opus"):
        return "audio/ogg"
    if ct in ("audio/mp4", "audio/m4a", "video/mp4"):
        return "audio/mp4"
    return "audio/wav"


async def _read_capped(file: UploadFile, max_bytes: int, too_large: HTTPException) -> bytes:
    """multipart 解析已把上传落到临时文件，``size`` 是实际字节数；读取仍按上限截断。"""
    if file.size is not None and file.size > max_bytes:
        raise too_large
    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise too_large
    return data


@router.get("/files/{file_id}")
async def serve_file(file_id: str) -> FileResponse:
    """提供临时媒体文件（公开 URL，供 LLM 访问，无需鉴权）。"""
    result = get_file_path(file_id)
    if result is None:
        raise HTTPException(status_code=404, detail="File not found or expired")
    path, content_type = result
    return FileResponse(path, media_type=content_type)


@router.get("/videos/{session_id}/{file_id}")
async def serve_session_video(session_id: str, file_id: str) -> FileResponse:
    """提供会话视频附件。公开（不鉴权）：file_id 为不可猜测 token，且公网模式下供应商需直接拉取。"""
    path = resolve_video_file(session_id, file_id)
    if path is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "Video not found", "reason": "not_found", "status_code": 404},
        )
    return FileResponse(path, media_type=video_mime_for_ext(path.suffix))


@router.post("/videos", response_model=ChatVideoUploadResponse)
@limiter.limit(lambda: f"{SETTINGS.media_video_rate_limit_per_minute}/minute")
async def upload_chat_video(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    file: UploadFile | None = File(None),
    session_id: str = Form(""),
) -> ChatVideoUploadResponse:
    """聊天视频附件上传：落会话目录（滚动配额）并返回附件 URL（本地相对 / 公网绝对）。"""
    if file is None:
        raise HTTPException(
            status_code=422,
            detail={"error": "Missing video file", "reason": "missing_video_file", "status_code": 422},
        )
    # 归一到无前导零形式：目录、附件 URL 与 conversation_id 三者必须同形，"007" 会造成磁盘/校验分裂。
    raw_session_id = session_id.strip()
    if not raw_session_id.isdigit() or len(raw_session_id) > 20:
        raise HTTPException(
            status_code=422,
            detail={"error": "Invalid session_id", "reason": "invalid_session_id", "status_code": 422},
        )
    session_id = str(int(raw_session_id))
    if await Conversation.by_session_id(db, session_id, user_id=user.id) is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "Session not found", "reason": "not_found", "status_code": 404},
        )

    ext = Path(file.filename or "").suffix.lower()
    if ext not in ATTACHMENT_VIDEO_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail={
                "error": f"Unsupported video format (allowed: {', '.join(sorted(ATTACHMENT_VIDEO_EXTENSIONS))})",
                "reason": "unsupported_video_format",
                "status_code": 415,
            },
        )

    # 本地模式 50MB；公网模式（public_base_url）供应商直接拉 URL，上限放宽到会话配额。
    session_quota = SETTINGS.attachment_session_quota_bytes
    max_bytes = session_quota if SETTINGS.public_base_url.strip() else ATTACHMENT_VIDEO_MAX_BYTES

    hint = "" if max_bytes == session_quota else "；配置 server.public_base_url 后可经公网 URL 发送更大文件"
    data = await _read_capped(
        file,
        max_bytes,
        HTTPException(
            status_code=413,
            detail={
                "error": f"Video too large (max {max_bytes // (1024 * 1024)} MB){hint}",
                "reason": "payload_too_large",
                "status_code": 413,
            },
        ),
    )

    # 配额滚动剔除发生在写盘前：保证新文件落得下，且引用行同步改写不产生死链。
    await enforce_session_quota(db, session_id, len(data))
    file_id, size = await asyncio.to_thread(save_video_attachment, session_id, data, ext)
    logger.info("chat video uploaded", extra={"session_id": session_id, "file_id": file_id, "size": size})
    return ChatVideoUploadResponse(url=attachment_video_url(session_id, file_id))


@router.post("/stt", response_model=SpeechToTextResponse)
@limiter.limit(lambda: f"{SETTINGS.media_stt_rate_limit_per_minute}/minute")
async def speech_to_text(
    request: Request,
    user: CurrentUser,
    audio_file: UploadFile | None = File(None),
    language: str = "",
) -> SpeechToTextResponse:
    """走供应商链路的语音转写。"""
    if audio_file is None:
        raise HTTPException(
            status_code=422,
            detail={"error": "Missing audio file", "reason": "missing_audio_file", "status": 422},
        )
    file_bytes = await _read_capped(
        audio_file,
        STT_MAX_AUDIO_BYTES,
        HTTPException(
            status_code=413,
            detail={
                "error": f"Audio file too large (max {STT_MAX_AUDIO_BYTES // (1024 * 1024)} MB)",
                "reason": "payload_too_large",
                "status": 413,
            },
        ),
    )
    mime_type = _resolve_mime_type(audio_file.content_type)

    try:
        text = await transcribe_audio(user.id, file_bytes, mime_type, language=language.strip() or "auto")
    except MissingLlmConfigError:
        raise missing_config_http("语音识别服务")
    except Exception as e:
        raise llm_http_error(e, "stt") from e
    return SpeechToTextResponse(text=text)


class TtsRequest(BaseModel):
    text: str = ""
    voice: str = ""
    language: str = ""


@router.post("/tts")
@limiter.limit(lambda: f"{SETTINGS.media_tts_rate_limit_per_minute}/minute")
async def text_to_speech(request: Request, body: TtsRequest, user: CurrentUser) -> StreamingResponse:
    """走供应商链路的语音合成。"""
    text = body.text.strip()
    voice = body.voice.strip()
    language = body.language.strip().lower()
    if not text:
        raise HTTPException(
            status_code=400,
            detail={"error": "text is required", "reason": "missing_params", "status": 400},
        )
    if len(text) > TTS_MAX_TEXT_CHARS:
        raise HTTPException(
            status_code=413,
            detail={"error": f"text exceeds {TTS_MAX_TEXT_CHARS} chars", "reason": "payload_too_large", "status": 413},
        )

    try:
        result = await synthesize_speech(user.id, text, voice, language)
    except MissingLlmConfigError:
        raise missing_config_http("语音合成服务")
    except Exception as e:
        raise llm_http_error(e, "tts") from e

    # 回传实际使用的音色，让 desktop 在供应商切换后保持同步。
    return StreamingResponse(
        iter([result.audio]),
        media_type=result.mime,
        headers={"X-Voice-Used": quote(result.voice or "", safe="")},
    )
