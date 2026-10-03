from typing import ClassVar

from ..base import STTProvider, STTResult
from ..openai_compat import transcribe_input_audio


class MiMoSTTProvider(STTProvider):
    """通过 MiMo chat/completions 的 input_audio 内容块提供 STT；请求体的 asr_options 传递 language。"""

    provider_name = "mimo"
    DEFAULT_BASE_URL: ClassVar[str] = "https://token-plan-cn.xiaomimimo.com/v1"
    DEFAULT_MODEL: ClassVar[str] = "mimo-v2.5-asr"

    async def transcribe(self, audio: bytes, *, mime_type: str = "audio/wav", language: str = "auto") -> STTResult:
        return await transcribe_input_audio(
            self.config,
            audio,
            mime_type=mime_type,
            asr_options={"language": language},
        )
