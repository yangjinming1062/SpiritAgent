"""STT 供应商链调用：REST 转写端点共用的服务层。已注册供应商的 ASR 均无增量音频输入能力，整段转写，无流式路径。"""

from components import SESSION_LOCAL

from .llm_client import resolve_provider_chain
from .llm_fallback import execute_with_fallback
from .providers import ServiceType, STTProvider


async def transcribe_audio(user_id: int, audio: bytes, mime_type: str, language: str = "auto") -> str:
    """走供应商链整段转写，返回识别文本；链解析为空抛 MissingLlmConfigError。"""
    async with SESSION_LOCAL() as db:
        chain = await resolve_provider_chain(db, user_id, ServiceType.stt)

    result = await execute_with_fallback(
        chain,
        STTProvider,
        lambda provider: provider.transcribe(audio, mime_type=mime_type, language=language),
        user_id=user_id,
    )
    return result.text
