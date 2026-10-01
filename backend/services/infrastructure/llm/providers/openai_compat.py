import base64

from .base import EmbeddingProvider, ProviderConfig, ProviderError, STTResult
from .http import get_async_client


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """任意 OpenAI 兼容 /embeddings 端点供应商共用基类。"""

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._client = get_async_client(config.api_key, config.base_url)

    def _request_dimensions(self) -> int | None:
        """需要供应商按指定宽度输出时返回维度；默认使用模型原生宽度。"""
        return None

    async def embed(self, texts: list[str], *, purpose: str = "db") -> list[list[float]]:
        if not texts:
            return []
        dimensions = self._request_dimensions()
        res = await self._client.embeddings.create(
            input=texts,
            model=self.config.model,
            **({"dimensions": dimensions} if dimensions is not None else {}),
        )
        items = sorted(res.data, key=lambda item: item.index)
        if [item.index for item in items] != list(range(len(texts))):
            raise ProviderError("embedding response indices do not match the input batch")
        return [item.embedding for item in items]


async def transcribe_input_audio(
    config: ProviderConfig,
    audio: bytes,
    *,
    mime_type: str,
    asr_options: dict[str, str] | None,
) -> STTResult:
    """经 chat.completions 的 input_audio 内容块整段转写；未正常结束视为失败。"""
    b64_audio = base64.b64encode(audio).decode("utf-8")
    response = await get_async_client(config.api_key, config.base_url).chat.completions.create(
        model=config.model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "input_audio", "input_audio": {"data": f"data:{mime_type};base64,{b64_audio}"}},
                ],
            },
        ],
        extra_body={"asr_options": asr_options} if asr_options is not None else {},
    )
    choice = response.choices[0] if response.choices else None
    if choice is not None and choice.finish_reason != "stop":
        raise RuntimeError(f"{config.provider_name} transcription did not complete: {choice.finish_reason}")
    text = (choice.message.content or "") if choice and choice.message else ""
    return STTResult(text=text.strip())
