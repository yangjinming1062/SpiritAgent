import base64
from typing import ClassVar

from openai import AsyncOpenAI

from ..base import ProviderConfig, STTProvider, STTResult
from ..http import get_async_client


class QwenSTTProvider(STTProvider):
    """通过千问 Qwen-ASR 的 chat.completions + input_audio 提供 STT（仅识别，不进入陪伴对话）。"""

    provider_name = "qwen"
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"stt": "qwen3-asr-flash"}
    DEFAULT_CONTEXT_TOKENS: ClassVar[dict[str, int]] = {"stt": 8_000}

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._client: AsyncOpenAI = get_async_client(config.api_key, config.base_url)

    def raw_client(self) -> AsyncOpenAI | None:
        return self._client

    async def transcribe(self, audio: bytes, *, mime_type: str = "audio/wav", language: str = "auto") -> STTResult:
        b64_audio = base64.b64encode(audio).decode("utf-8")
        asr_options: dict = {}
        if language and language != "auto":
            asr_options["language"] = language
        response = await self._client.chat.completions.create(
            model=self.config.model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_audio", "input_audio": {"data": f"data:{mime_type};base64,{b64_audio}"}},
                    ],
                },
            ],
            extra_body={"asr_options": asr_options} if asr_options else {},
        )
        choice = response.choices[0] if response.choices else None
        text = (choice.message.content or "") if choice and choice.message else ""
        return STTResult(text=text.strip(), raw=response)
