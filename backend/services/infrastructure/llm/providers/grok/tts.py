from typing import ClassVar

from modules.media import SpeechStyle

from .._provider_errors import raise_for_provider_response
from ..base import ProviderConfig, TTSProvider, TTSResult, pick_catalog_voice
from ..http import get_http

# xAI 内置音色目录的本地子集；合成仅消费下方目录。
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
    """通过 xAI 的单次 POST /v1/tts 提供 TTS（请求 {text, voice_id, language, output_format{codec,sample_rate,bit_rate}}）；不支持 model 字段；language 必填（BCP-47 或 "auto"），内置目录仅英文音色，language 固定 en，不在目录内的音色回退到目录首位；200 直接返回音频字节（默认 MP3）。"""

    provider_name = "grok"
    DEFAULT_BASE_URL: ClassVar[str] = "https://api.x.ai/v1"
    DEFAULT_MODEL: ClassVar[str] = "grok-voice-think-fast-1.0"
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

    async def synthesize(self, text: str, *, voice: str, speech_style: SpeechStyle | None) -> TTSResult:
        chosen_voice = pick_catalog_voice(voice, self.VOICE_CATALOG, provider=self.provider_name)
        payload = {
            "text": text,
            "voice_id": chosen_voice,
            "language": "en",
            "output_format": {"codec": "mp3", "sample_rate": 24000, "bit_rate": 128000},
        }
        resp = await self._client.post("/tts", json=payload)
        # TTS 200 直接返回音频字节；raise_for_provider_response 检查 JSON 信封，体非 JSON 时返回 {}。
        raise_for_provider_response(resp, family=self.provider_name)
        return TTSResult(audio=resp.content, mime="audio/mpeg", voice=chosen_voice)
