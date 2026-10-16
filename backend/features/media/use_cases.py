"""媒体请求用例；校验输入，返回媒体快照，由入口转换响应。"""

from infrastructure.ai.llm_client import MissingLlmConfigError
from infrastructure.ai.stt_service import transcribe_audio
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.constants import STT_MAX_AUDIO_BYTES, TTS_MAX_TEXT_CHARS
from infrastructure.request_errors import RequestError
from infrastructure.temp_files import get_file_path
from infrastructure.uploads import UploadSource

from features.media.http_contracts import MediaFile, ProviderOperationError, SpeechAudio, TtsRequest
from features.media.speech import synthesize_speech
from features.media.speech_contracts import SpeechToTextResponse


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


async def _read_capped(file: UploadSource, max_bytes: int, too_large: RequestError) -> bytes:
    """multipart 解析已把上传落到临时文件，``size`` 是实际字节数；读取仍按上限截断。"""
    if file.size is not None and file.size > max_bytes:
        raise too_large
    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise too_large
    return data


async def serve_file(file_id: str) -> MediaFile:
    """提供临时媒体文件（公开 URL，供 LLM 访问，无需鉴权）。"""
    result = get_file_path(file_id)
    if result is None:
        raise RequestError(status_code=404, message="File not found or expired")
    path, content_type = result
    return MediaFile(path=path, media_type=content_type)


async def speech_to_text(
    user_id: int,
    audio_file: UploadSource | None = None,
    language: str = "",
) -> SpeechToTextResponse:
    """走供应商链路的语音转写。"""
    if audio_file is None:
        raise RequestError(
            status_code=422,
            message="Missing audio file",
            code="missing_audio_file",
        )
    file_bytes = await _read_capped(
        audio_file,
        STT_MAX_AUDIO_BYTES,
        RequestError(
            status_code=413,
            message=f"Audio file too large (max {STT_MAX_AUDIO_BYTES // (1024 * 1024)} MB)",
            code="payload_too_large",
        ),
    )
    mime_type = _resolve_mime_type(audio_file.content_type)
    try:
        text = await transcribe_audio(user_id, file_bytes, mime_type, language=language.strip() or "auto")
    except MissingLlmConfigError:
        raise
    except Exception as e:
        raise ProviderOperationError(e, "stt") from e
    return SpeechToTextResponse(text=text)


async def text_to_speech(body: TtsRequest, user_id: int) -> SpeechAudio:
    """走供应商链路的语音合成。"""
    text = body.text.strip()
    voice = body.voice.strip()
    language = body.language.strip().lower()
    if not text:
        raise RequestError(
            status_code=400,
            message="text is required",
            code="missing_params",
        )
    if len(text) > TTS_MAX_TEXT_CHARS:
        raise RequestError(
            status_code=413,
            message=f"text exceeds {TTS_MAX_TEXT_CHARS} chars",
            code="payload_too_large",
        )
    try:
        result = await synthesize_speech(user_id, text, voice, language, task=AiTaskKey.speech_preview)
    except MissingLlmConfigError:
        raise
    except Exception as e:
        raise ProviderOperationError(e, "tts") from e
    return SpeechAudio(audio=result.audio, mime=result.mime, voice=result.voice or "")
