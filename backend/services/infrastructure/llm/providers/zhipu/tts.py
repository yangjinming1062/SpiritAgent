import logging
from typing import ClassVar

from modules.media import SpeechStyle

from .._provider_errors import raise_for_provider_response
from ..base import ProviderConfig, TTSProvider, TTSResult, pick_catalog_voice
from ..http import get_http

logger = logging.getLogger(__name__)


class ZhipuTTSProvider(TTSProvider):
    """通过 Zhipu 的 POST /audio/speech 提供 TTS；模型 glm-tts，默认音色 tongtong；响应格式跟随 fmt 参数，mime 由其派生。"""

    provider_name = "zhipu"
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"tts": "glm-tts"}
    DEFAULT_CONTEXT_TOKENS: ClassVar[dict[str, int]] = {"tts": 8_000}
    VOICE_CATALOG: ClassVar[list[dict]] = [
        {
            "id": "tongtong",
            "label": "彤彤",
            "gender": "female",
            "language": "zh",
            "tags": ["温柔", "自然", "女", "甜", "默认", "中文"],
            "description": "温柔自然的默认女声。",
        },
        {
            "id": "chuichui",
            "label": "锤锤",
            "gender": "neutral",
            "language": "zh",
            "tags": ["活泼", "俏皮", "中文"],
            "description": "活泼俏皮的声音。",
        },
        {
            "id": "xiaochen",
            "label": "小陈",
            "gender": "neutral",
            "language": "zh",
            "tags": ["清晰", "自然", "中文"],
            "description": "清晰自然的声音。",
        },
        {
            "id": "jam",
            "label": "Jam",
            "gender": "neutral",
            "language": "zh",
            "tags": ["活泼", "可爱", "中文", "动物"],
            "description": "动动动物圈 Jam 音色。",
        },
        {
            "id": "kazi",
            "label": "Kazi",
            "gender": "neutral",
            "language": "zh",
            "tags": ["沉稳", "中文", "动物"],
            "description": "动动动物圈 Kazi 音色。",
        },
        {
            "id": "douji",
            "label": "豆鸡",
            "gender": "neutral",
            "language": "zh",
            "tags": ["俏皮", "搞笑", "中文", "动物"],
            "description": "动动动物圈豆鸡音色。",
        },
        {
            "id": "luodo",
            "label": "萝卜",
            "gender": "neutral",
            "language": "zh",
            "tags": ["温柔", "中文", "动物"],
            "description": "动动动物圈萝卜音色。",
        },
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
        response_format = fmt or "wav"
        chosen_voice = pick_catalog_voice(voice, self.VOICE_CATALOG)
        if voice != chosen_voice:
            logger.info("zhipu tts: substituted voice", extra={"requested": voice, "used": chosen_voice})
        payload: dict = {
            "model": self.config.model,
            "input": text,
            "voice": chosen_voice,
            "response_format": response_format,
        }
        if speed is not None:
            payload["speed"] = speed

        resp = await self._client.post("/audio/speech", json=payload)

        # TTS 成功（200）直接返回音频字节；raise_for_provider_response 检查 JSON 信封，体非 JSON 时返回 {}。
        raise_for_provider_response(resp, family=self.provider_name, model=self.config.model)

        mime = "audio/mpeg" if response_format == "mp3" else f"audio/{response_format}"
        return TTSResult(audio=resp.content, mime=mime, voice=chosen_voice)
