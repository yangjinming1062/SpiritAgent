"""STT 供应商链调用：REST 转写端点共用的服务层。

四家供应商的 ASR 均无增量音频输入能力（响应侧 SSE 只是把整段结果分块吐回），整段转写，无流式路径。
"""

from components import SESSION_LOCAL

from .llm_client import MissingLlmConfigError, resolve_provider_chain
from .llm_fallback import execute_with_fallback


async def transcribe_audio(user_id: int, audio: bytes, mime_type: str, language: str = "auto") -> str:
    """走供应商链整段转写，返回识别文本；链解析为空抛 MissingLlmConfigError。"""
    async with SESSION_LOCAL() as db:
        chain = await resolve_provider_chain(db, user_id, "stt")
    if not chain:
        raise MissingLlmConfigError()
    result = await execute_with_fallback(
        db=None,
        user_id=user_id,
        service_type="stt",
        call_fn=lambda p: p.transcribe(audio, mime_type=mime_type, language=language),
        _chain=chain,
    )
    return result.text
