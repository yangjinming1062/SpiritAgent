from urllib.parse import quote

from fastapi import File, Form, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from features.conversation import media_uploads as _uploads
from features.conversation.speech_contracts import ChatAudioUploadResponse, ChatVideoUploadResponse
from features.media import use_cases as _media
from features.media.http_contracts import ProviderOperationError, TtsRequest
from features.media.speech_contracts import SpeechToTextResponse
from features.media.voice_contracts import VoiceSampleResponse
from features.media.voices import upload_voice_sample
from infrastructure.ai.llm_client import MissingLlmConfigError
from infrastructure.config import SETTINGS
from infrastructure.database import DbSession
from infrastructure.http_contracts import operation_policy

from entrypoints.http.auth import CurrentUser
from entrypoints.http.rate_limit import limiter
from entrypoints.http.routes._http_errors import missing_config_error, provider_request_error
from entrypoints.http.routing import get_router

router = get_router()


@router.post(
    "/voice-samples",
    response_model=VoiceSampleResponse,
    openapi_extra=operation_policy(identities=("desktop", "remote"), upload="audio"),
)
@limiter.limit(lambda: f"{SETTINGS.media_stt_rate_limit_per_minute}/minute")
async def upload_voice_reference(
    request: Request,
    user: CurrentUser,
    file: UploadFile = File(...),
) -> VoiceSampleResponse:
    return await upload_voice_sample(user.id, file)


@router.get("/files/{file_id}", openapi_extra=operation_policy(identities=("public",)))
async def serve_file(file_id: str) -> FileResponse:
    """提供临时媒体文件（公开 URL，供 LLM 访问，无需鉴权）。"""
    result = await _media.serve_file(file_id=file_id)
    return FileResponse(result.path, media_type=result.media_type)


@router.get("/videos/{session_id}/{file_id}", openapi_extra=operation_policy(identities=("public",)))
async def serve_session_video(session_id: str, file_id: str) -> FileResponse:
    """提供会话视频附件。公开（不鉴权）：file_id 为不可猜测 token，且公网模式下供应商需直接拉取。"""
    result = await _uploads.serve_session_video(session_id=session_id, file_id=file_id)
    return FileResponse(result.path, media_type=result.media_type)


@router.get("/audios/{session_id}/{file_id}", openapi_extra=operation_policy(identities=("desktop", "remote")))
async def serve_session_audio(session_id: str, file_id: str, db: DbSession, user: CurrentUser) -> FileResponse:
    result = await _uploads.serve_session_audio(session_id=session_id, file_id=file_id, db=db, user_id=user.id)
    return FileResponse(result.path, media_type=result.media_type)


@router.post(
    "/audios",
    response_model=ChatAudioUploadResponse,
    openapi_extra=operation_policy(identities=("desktop", "remote"), upload="audio"),
)
@limiter.limit(lambda: f"{SETTINGS.media_stt_rate_limit_per_minute}/minute")
async def upload_chat_audio(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    file: UploadFile = File(...),
    session_id: str = Form(...),
) -> ChatAudioUploadResponse:
    return await _uploads.upload_chat_audio(db=db, user_id=user.id, file=file, session_id=session_id)


@router.post(
    "/videos",
    response_model=ChatVideoUploadResponse,
    openapi_extra=operation_policy(identities=("desktop", "remote"), upload="video"),
)
@limiter.limit(lambda: f"{SETTINGS.media_video_rate_limit_per_minute}/minute")
async def upload_chat_video(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    file: UploadFile | None = File(None),
    session_id: str = Form(""),
) -> ChatVideoUploadResponse:
    """聊天视频附件上传：落会话目录（滚动配额）并返回附件 URL（本地相对 / 公网绝对）。"""
    return await _uploads.upload_chat_video(db=db, user_id=user.id, file=file, session_id=session_id)


@router.post(
    "/stt",
    response_model=SpeechToTextResponse,
    openapi_extra=operation_policy(identities=("desktop", "remote"), upload="audio", wait_for_model=True),
)
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
    except MissingLlmConfigError:
        raise missing_config_error("语音识别服务")
    except ProviderOperationError as exc:
        raise provider_request_error(exc.error, exc.operation) from exc.error


@router.post("/tts", openapi_extra=operation_policy(identities=("desktop", "remote"), wait_for_model=True))
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
    except MissingLlmConfigError:
        raise missing_config_error("语音合成服务")
    except ProviderOperationError as exc:
        raise provider_request_error(exc.error, exc.operation) from exc.error
