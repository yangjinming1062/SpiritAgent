from urllib.parse import quote

from fastapi import File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from features.conversation import media_uploads as _uploads
from features.conversation.speech_contracts import ChatAudioUploadResponse, ChatVideoUploadResponse
from features.media import use_cases as _media
from features.media.http_contracts import ProviderOperationError, TtsRequest
from features.media.speech_contracts import SpeechToTextResponse
from infrastructure.ai.llm_client import MissingLlmConfigError
from infrastructure.config import SETTINGS
from infrastructure.database import DbSession
from infrastructure.request_errors import RequestError

from entrypoints.http.auth import CurrentUser
from entrypoints.http.rate_limit import limiter
from entrypoints.http.routes._http_errors import llm_http_error, missing_config_http
from entrypoints.http.routing import get_router

router = get_router()


@router.get("/files/{file_id}")
async def serve_file(file_id: str) -> FileResponse:
    """提供临时媒体文件（公开 URL，供 LLM 访问，无需鉴权）。"""
    try:
        result = await _media.serve_file(file_id=file_id)
        return FileResponse(result.path, media_type=result.media_type)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get("/videos/{session_id}/{file_id}")
async def serve_session_video(session_id: str, file_id: str) -> FileResponse:
    """提供会话视频附件。公开（不鉴权）：file_id 为不可猜测 token，且公网模式下供应商需直接拉取。"""
    try:
        result = await _uploads.serve_session_video(session_id=session_id, file_id=file_id)
        return FileResponse(result.path, media_type=result.media_type)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get("/audios/{session_id}/{file_id}")
async def serve_session_audio(session_id: str, file_id: str, db: DbSession, user: CurrentUser) -> FileResponse:
    try:
        result = await _uploads.serve_session_audio(session_id=session_id, file_id=file_id, db=db, user_id=user.id)
        return FileResponse(result.path, media_type=result.media_type)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/audios", response_model=ChatAudioUploadResponse)
@limiter.limit(lambda: f"{SETTINGS.media_stt_rate_limit_per_minute}/minute")
async def upload_chat_audio(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    file: UploadFile = File(...),
    session_id: str = Form(...),
) -> ChatAudioUploadResponse:
    try:
        return await _uploads.upload_chat_audio(db=db, user_id=user.id, file=file, session_id=session_id)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


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
    try:
        return await _uploads.upload_chat_video(db=db, user_id=user.id, file=file, session_id=session_id)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/stt", response_model=SpeechToTextResponse)
@limiter.limit(lambda: f"{SETTINGS.media_stt_rate_limit_per_minute}/minute")
async def speech_to_text(
    request: Request,
    user: CurrentUser,
    audio_file: UploadFile | None = File(None),
    language: str = "",
) -> SpeechToTextResponse:
    """走供应商链路的语音转写。"""
    try:
        return await _media.speech_to_text(user_id=user.id, audio_file=audio_file, language=language)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except MissingLlmConfigError:
        raise missing_config_http("语音识别服务")
    except ProviderOperationError as exc:
        raise llm_http_error(exc.error, exc.operation) from exc.error


@router.post("/tts")
@limiter.limit(lambda: f"{SETTINGS.media_tts_rate_limit_per_minute}/minute")
async def text_to_speech(request: Request, body: TtsRequest, user: CurrentUser) -> StreamingResponse:
    """走供应商链路的语音合成。"""
    try:
        result = await _media.text_to_speech(body=body, user_id=user.id)
        return StreamingResponse(
            iter([result.audio]),
            media_type=result.mime,
            headers={"X-Voice-Used": quote(result.voice, safe="")},
        )
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except MissingLlmConfigError:
        raise missing_config_http("语音合成服务")
    except ProviderOperationError as exc:
        raise llm_http_error(exc.error, exc.operation) from exc.error
