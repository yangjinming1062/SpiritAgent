from typing import ClassVar

from ..base import ProviderConfig, STTProvider, STTResult
from ..http import get_http
from ._errors import raise_for_minimax_response


class MiniMaxSTTProvider(STTProvider):
    """通过 MiniMax 的 multipart POST /v1/speech_to_text 提供 STT（{model,file} 表单；language 以 BCP-47 同名 HTTP 请求头提示主要语言，不传=混合语言识别）；响应 {"text","duration","trace_id"}；支持 wav/aiff/flac/alac(m4a)/mp3/aac/opus/ogg，≤500 秒 / ≤50 MB，webm 不受支持（上游 400 由供应商回退链接管）。"""

    provider_name = "minimax"
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"stt": "asr-1.0"}
    DEFAULT_CONTEXT_TOKENS: ClassVar[dict[str, int]] = {"stt": 8_000}

    # 上传侧归一化只产这几种 MIME；扩展名与 MiniMax 支持格式对齐，其余（含默认 audio/wav）按 wav 命名。
    _EXT_BY_MIME: ClassVar[dict[str, str]] = {
        "audio/mpeg": "mp3",
        "audio/ogg": "ogg",
        "audio/mp4": "m4a",
        "audio/webm": "webm",
    }

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._client = get_http(config.base_url, config.api_key)

    async def transcribe(self, audio: bytes, *, mime_type: str = "audio/wav", language: str = "auto") -> STTResult:
        ext = self._EXT_BY_MIME.get(mime_type, "wav")
        files = {"file": (f"audio.{ext}", audio, mime_type)}
        data = {"model": self.config.model}
        headers = {"language": language} if language and language != "auto" else None

        resp = await self._client.post("/v1/speech_to_text", files=files, data=data, headers=headers)
        body = raise_for_minimax_response(resp, provider=self.provider_name, model=self.config.model)
        return STTResult(text=body.get("text", "").strip(), raw=body)
