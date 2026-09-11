import base64
from typing import ClassVar

from openai import AsyncOpenAI

from ..base import ProviderConfig, STTProvider, STTResult
from ..http import get_async_client


class MiMoSTTProvider(STTProvider):
    """通过 MiMo 的 input_audio 内容块 + asr_options 体提供 STT；POST /v1/chat/completions，body 含 messages=[{user,[{input_audio,...}]}] 与 extra_body.asr_options={language}。"""

    provider_name = "mimo"
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"stt": "mimo-v2.5-asr"}
    DEFAULT_CONTEXT_TOKENS: ClassVar[dict[str, int]] = {"stt": 8_000}

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._client: AsyncOpenAI = get_async_client(config.api_key, config.base_url)

    def raw_client(self) -> AsyncOpenAI | None:
        return self._client

    async def transcribe(self, audio: bytes, *, mime_type: str = "audio/wav", language: str = "auto") -> STTResult:
        b64_audio = base64.b64encode(audio).decode("utf-8")
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
            extra_body={"asr_options": {"language": language}},
        )
        choice = response.choices[0] if response.choices else None
        text = (choice.message.content or "") if choice and choice.message else ""
        return STTResult(text=text, raw=response)
