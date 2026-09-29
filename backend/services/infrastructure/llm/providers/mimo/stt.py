from typing import ClassVar

from ..base import STTProvider, STTResult
from ..openai_compat import transcribe_input_audio


class MiMoSTTProvider(STTProvider):
    """通过 MiMo 的 input_audio 内容块 + asr_options 体提供 STT；POST /v1/chat/completions，body 含 messages=[{user,[{input_audio,...}]}] 与 extra_body.asr_options={language}。"""

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
