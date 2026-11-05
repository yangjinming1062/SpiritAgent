"""媒体请求用例；校验输入，返回媒体快照，由入口转换响应。"""

from infrastructure.ai.llm_client import MissingLlmConfigError
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.constants import TTS_MAX_TEXT_CHARS
from infrastructure.request_errors import RequestError
from infrastructure.temp_files import get_file_path

from features.media.http_contracts import MediaFile, ProviderOperationError, SpeechAudio, TtsRequest
from features.media.speech import synthesize_speech


async def serve_file(file_id: str) -> MediaFile:
    """提供临时媒体文件（公开 URL，供 LLM 访问，无需鉴权）。"""
    result = get_file_path(file_id)
    if result is None:
        raise RequestError(status_code=404, message="File not found or expired")
    path, content_type = result
    return MediaFile(path=path, media_type=content_type)


async def text_to_speech(body: TtsRequest, user_id: int) -> SpeechAudio:
    """走供应商链路的语音合成。"""
    text = body.text.strip()
    voice = body.voice.strip()
    language = body.language.strip().lower()
    if not text:
        raise RequestError(status_code=400, message="text is required", code="missing_params")
    if len(text) > TTS_MAX_TEXT_CHARS:
        raise RequestError(
            status_code=413, message=f"text exceeds {TTS_MAX_TEXT_CHARS} chars", code="payload_too_large"
        )
    try:
        result = await synthesize_speech(user_id, text, voice, language, task=AiTaskKey.speech_preview)
    except MissingLlmConfigError:
        raise
    except Exception as e:
        raise ProviderOperationError(e, "tts") from e
    return SpeechAudio(audio=result.audio, mime=result.mime, voice=result.voice or "")
