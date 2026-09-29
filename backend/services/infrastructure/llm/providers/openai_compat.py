import base64

import httpx

from .base import EmbeddingProvider, ProviderConfig, ProviderError, STTResult
from .http import get_async_client


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """任意 OpenAI 兼容 /embeddings 端点供应商共用基类。"""

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._client = get_async_client(config.api_key, config.base_url)

    async def embed(self, texts: list[str], *, purpose: str = "db") -> list[list[float]]:
        if not texts:
            return []
        try:
            res = await self._client.embeddings.create(input=texts, model=self.config.model)
            items = sorted(res.data, key=lambda item: item.index)
            if [item.index for item in items] != list(range(len(texts))):
                raise ValueError("embedding response indices do not match the input batch")
            return [item.embedding for item in items]
        except Exception as exc:
            # 保留 status_code + 结构化 body，供错误分类读取错误码；只重抛消息会退化为文本匹配。
            body = getattr(exc, "body", None)
            if body is None:
                response = getattr(exc, "response", None)
                if isinstance(response, httpx.Response):
                    try:
                        json_body = response.json()
                        body = json_body if isinstance(json_body, dict) else None
                    except Exception:
                        body = None
            raise ProviderError(
                f"{self.provider_name} embedding error: {exc}",
                status_code=getattr(exc, "status_code", None),
                body=body,
            ) from exc


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
    return STTResult(text=text.strip(), raw=response)
