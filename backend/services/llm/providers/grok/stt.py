from typing import ClassVar

from .._provider_errors import raise_for_provider_response
from ..base import ProviderConfig, STTProvider, STTResult
from ..http import get_http


class GrokSTTProvider(STTProvider):
    """通过 xAI 的单次 POST /v1/stt 提供 STT（multipart 形态，file 字段为音频字节，url 字段为服务端下载替代）；xAI 无 model 选择器，底层固定 grok-transcribe；language 由 xAI 自动识别故不生效，仅为符合 STTProvider ABC 契约保留；响应 {"text","language","duration","words"?}。"""

    provider_name = "grok"
    # grok-transcribe 是底层模型（见流式 STT 文档）；单次端点不接受选择器，此默认值仅为 registry 镜像的元数据，不会随请求发出。
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"stt": "grok-transcribe"}
    DEFAULT_CONTEXT_TOKENS: ClassVar[dict[str, int]] = {"stt": 8_000}

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._client = get_http(config.base_url, config.api_key)

    async def transcribe(self, audio: bytes, *, mime_type: str = "audio/wav", language: str = "auto") -> STTResult:
        # xAI 通过文件头自动识别容器格式，multipart 文件名只需看起来合理。
        if "wav" in mime_type:
            ext = "wav"
        elif "mpeg" in mime_type or "mp3" in mime_type:
            ext = "mp3"
        elif "ogg" in mime_type:
            ext = "ogg"
        elif "flac" in mime_type:
            ext = "flac"
        elif "m4a" in mime_type or "mp4" in mime_type:
            ext = "m4a"
        elif "webm" in mime_type:
            ext = "webm"
        else:
            ext = "wav"

        files = {"file": (f"audio.{ext}", audio, mime_type)}

        resp = await self._client.post("/stt", files=files)
        body = raise_for_provider_response(resp, family=self.provider_name, model=self.config.model)
        text = body.get("text", "")
        return STTResult(text=text.strip(), raw=body)
