from typing import ClassVar

from ..base import STTProvider, STTResult
from ..openai_compat import transcribe_input_audio


class QwenSTTProvider(STTProvider):
    """通过千问 Qwen-ASR 的 chat.completions + input_audio 提供 STT（仅识别，不进入陪伴对话）。"""

    provider_name = "qwen"
    DEFAULT_BASE_URL: ClassVar[str] = "https://maas.qianwenaiapi.com/compatible-mode/v1"
    DEFAULT_MODEL: ClassVar[str] = "qwen3-asr-flash"

    async def transcribe(self, audio: bytes, *, mime_type: str = "audio/wav", language: str = "auto") -> STTResult:
        return await transcribe_input_audio(
            self.config,
            audio,
            mime_type=mime_type,
            asr_options={"language": language} if language and language != "auto" else None,
        )
