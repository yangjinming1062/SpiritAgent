import logging
from typing import ClassVar

from modules.media import SpeechStyle

from .._provider_errors import raise_for_provider_response
from ..base import ProviderConfig, TTSProvider, TTSResult, pick_catalog_voice
from ..http import get_http

logger = logging.getLogger(__name__)


# xAI 已发布内置音色目录的子集（https://docs.x.ai/docs/rest-api-reference/inference/voice — GET /v1/tts/voices）；按性别/音色分桶每桶取一条；运行时若配置了 API key 可向运行端点扩展。
_GROK_VOICES: tuple[tuple[str, str, str], ...] = (
    ("eve", "Eve", "female"),  # docs default
    ("ara", "Ara", "female"),
    ("sal", "Sal", "neutral"),
    ("rex", "Rex", "male"),
    ("leo", "Leo", "male"),
    ("luna", "Luna", "female"),
    ("orion", "Orion", "neutral"),
    ("atlas", "Atlas", "male"),
)


class GrokTTSProvider(TTSProvider):
    """通过 xAI 的单次 POST /v1/tts 提供 TTS（请求 {text, voice_id, language, output_format{codec,sample_rate,bit_rate}, speed}）；不支持 model 字段；language 必填（BCP-47 或 "auto"），内置目录仅英文故默认 en，中文音色依赖用户自定义 ID；200 直接返回音频字节（默认 MP3）。"""

    provider_name = "grok"
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"tts": "grok-voice-think-fast-1.0"}
    DEFAULT_CONTEXT_TOKENS: ClassVar[dict[str, int]] = {"tts": 8_000}
    VOICE_CATALOG: ClassVar[list[dict]] = [
        {
            "id": voice_id,
            "label": label,
            "gender": gender,
            "language": "en",
            "tags": [label, "英文"],
            "description": f"xAI built-in voice: {label}",
        }
        for voice_id, label, gender in _GROK_VOICES
    ]

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._client = get_http(config.base_url, config.api_key)

    async def synthesize(
        self,
        text: str,
        *,
        voice: str = "",
        fmt: str = "mp3",
        speed: float | None = None,
        speech_style: SpeechStyle | None = None,
    ) -> TTSResult:
        chosen_voice = pick_catalog_voice(voice, self.VOICE_CATALOG)
        if voice != chosen_voice:
            logger.info("grok tts: substituted voice", extra={"requested": voice, "used": chosen_voice})

        codec = fmt or "mp3"
        output_format: dict = {"codec": codec, "sample_rate": 24000}
        if codec == "mp3":
            output_format["bit_rate"] = 128000

        payload: dict = {"text": text, "voice_id": chosen_voice, "language": "en", "output_format": output_format}
        if speed is not None:
            payload["speed"] = speed

        resp = await self._client.post("/tts", json=payload)

        # TTS 200 直接返回音频字节；raise_for_provider_response 检查 JSON 信封，体非 JSON 时返回 {}，与 zhipu/tts.py 保持同一模式。
        raise_for_provider_response(resp, family=self.provider_name, model=self.config.model)

        mime = "audio/mpeg" if codec == "mp3" else f"audio/{codec}"
        return TTSResult(audio=resp.content, mime=mime, voice=chosen_voice)
