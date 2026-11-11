"""STT 供应商链调用：整段路径与实时增量路径共存。"""

from collections.abc import AsyncIterable, AsyncIterator

from infrastructure.ai.llm_client import MissingLlmConfigError, build_provider, resolve_provider_chain
from infrastructure.ai.llm_fallback import execute_with_fallback
from infrastructure.ai.providers.base import ProviderCapabilityError, ServiceType, STTProvider, STTStreamEvent
from infrastructure.ai.providers.registry import provider_supports_streaming
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.database import SESSION_LOCAL


async def transcribe_audio(user_id: int, audio: bytes, mime_type: str, language: str = "auto") -> str:
    """走供应商链整段转写，返回识别文本；链解析为空抛 MissingLlmConfigError。"""
    async with SESSION_LOCAL() as db:
        chain = await resolve_provider_chain(db, user_id, ServiceType.stt, task=AiTaskKey.speech_input)

    result = await execute_with_fallback(
        chain,
        STTProvider,
        lambda provider: provider.transcribe(audio, mime_type=mime_type, language=language),
        user_id=user_id,
    )
    return result.text


async def stream_transcribe_audio(
    user_id: int,
    audio: AsyncIterable[bytes],
    *,
    mime_type: str = "audio/pcm;rate=24000",
    language: str = "auto",
) -> AsyncIterator[STTStreamEvent]:
    """使用明确声明流式能力的首个 STT 配置。

    流式输入不可无损重放，因此这里不会像整段请求一样在中途切换供应商；供应商必须
    在进入通话前已通过能力筛选，运行时错误由通话协调层按当前回合处理。
    """
    async with SESSION_LOCAL() as db:
        chain = await resolve_provider_chain(db, user_id, ServiceType.stt, task=AiTaskKey.speech_input)
    if not chain:
        raise MissingLlmConfigError("no STT provider configured")
    streaming = [
        config for config in chain if provider_supports_streaming(ServiceType.stt, config.provider_name, config)
    ]
    if not streaming:
        raise ProviderCapabilityError("configured STT providers do not support streaming")
    provider = build_provider(streaming[0], STTProvider)
    async for event in provider.transcribe_stream(audio, mime_type=mime_type, language=language):
        yield event
