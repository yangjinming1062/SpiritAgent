import base64
from typing import ClassVar

from modules.media import SpeechStyle

from ..base import ProviderConfig, TTSProvider, TTSResult, pick_catalog_voice
from ..http import download_bytes, get_http
from ._errors import raise_for_qwen_response


class QwenTTSProvider(TTSProvider):
    """通过千问 DashScope 多模态生成接口提供非实时 TTS（默认 qwen3-tts-instruct-flash）。"""

    provider_name = "qwen"
    DEFAULT_BASE_URL: ClassVar[str] = "https://maas.qianwenaiapi.com/api/v1"
    DEFAULT_MODEL: ClassVar[str] = "qwen3-tts-instruct-flash"
    VOICE_CATALOG: ClassVar[list[dict]] = [
        {
            "id": "Cherry",
            "label": "樱桃",
            "gender": "female",
            "language": "zh",
            "tags": ["阳光", "亲切", "自然", "女", "默认", "中文"],
            "description": "阳光积极、亲切自然小姐姐。",
        },
        {
            "id": "Serena",
            "label": "塞雷娜",
            "gender": "female",
            "language": "zh",
            "tags": ["温柔", "女", "中文"],
            "description": "温柔小姐姐。",
        },
        {
            "id": "Ethan",
            "label": "伊森",
            "gender": "male",
            "language": "zh",
            "tags": ["阳光", "温暖", "活力", "男", "中文"],
            "description": "标准普通话，阳光温暖有朝气。",
        },
        {
            "id": "Chelsie",
            "label": "切尔西",
            "gender": "female",
            "language": "zh",
            "tags": ["二次元", "女友", "女", "中文"],
            "description": "二次元虚拟女友声线。",
        },
        {
            "id": "Jennifer",
            "label": "珍妮弗",
            "gender": "female",
            "language": "en",
            "tags": ["美语", "电影质感", "女", "英文"],
            "description": "品牌级、电影质感美语女声。",
        },
        {
            "id": "Ryan",
            "label": "瑞安",
            "gender": "male",
            "language": "en",
            "tags": ["美语", "张力", "男", "英文"],
            "description": "节奏拉满、戏感强的美语男声。",
        },
        {
            "id": "Katerina",
            "label": "卡特琳娜",
            "gender": "female",
            "language": "zh",
            "tags": ["御姐", "韵律", "女", "中文"],
            "description": "御姐音色，韵律回味十足。",
        },
        {
            "id": "Nini",
            "label": "妮妮",
            "gender": "female",
            "language": "zh",
            "tags": ["软糯", "甜", "女", "中文"],
            "description": "软糯甜美的女声。",
        },
        {
            "id": "Bunny",
            "label": "邦尼",
            "gender": "female",
            "language": "zh",
            "tags": ["萌", "萝莉", "女", "中文"],
            "description": "萌属性爆棚的小萝莉。",
        },
        {
            "id": "Neil",
            "label": "尼尔",
            "gender": "male",
            "language": "zh",
            "tags": ["新闻", "播音", "男", "中文"],
            "description": "字正腔圆的新闻主持人声线。",
        },
    ]

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._client = get_http(config.base_url, config.api_key)

    async def synthesize(self, text: str, *, voice: str, speech_style: SpeechStyle | None) -> TTSResult:
        chosen_voice = pick_catalog_voice(voice, self.VOICE_CATALOG, provider=self.provider_name)
        payload = {
            "model": self.config.model,
            "input": {"text": text, "voice": chosen_voice},
        }
        resp = await self._client.post("/services/aigc/multimodal-generation/generation", json=payload)
        body = raise_for_qwen_response(resp)

        audio = (body.get("output") or {}).get("audio") or {}
        if data_b64 := audio.get("data"):
            raw = base64.b64decode(data_b64)
        elif url := audio.get("url"):
            # 临时 URL 24h 过期，下载后交付字节
            raw = await download_bytes(url)
        else:
            raise RuntimeError(f"qwen tts returned no audio: {body}")
        return TTSResult(audio=raw, mime="audio/mpeg", voice=chosen_voice)
