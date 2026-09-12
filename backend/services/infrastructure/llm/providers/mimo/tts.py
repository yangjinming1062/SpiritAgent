import base64
import logging
from collections.abc import AsyncIterator
from typing import ClassVar

from components import MAX_VOICE_DESIGN_PROMPT_CHARS
from modules.media import SpeechStyle
from openai import AsyncOpenAI

from ..base import AudioChunk, ProviderConfig, TTSProvider, TTSResult, VoiceDesignResult, pick_catalog_voice
from ..http import get_async_client
from ..speech_style import speech_style_matches, styled_speech_text

logger = logging.getLogger(__name__)

_VOICEDESIGN_MODEL = "mimo-v2.5-tts-voicedesign"
_VOICEDESIGN_PREFIX = "mimo_voicedesign:"

# 官方文档：流式调用（stream=True）要求 format=pcm16，输出为 24kHz PCM16LE mono。
_STREAM_SAMPLE_RATE = 24_000


class MiMoTTSProvider(TTSProvider):
    """通过 MiMo 在 Chat Completions 上的 audio={...} 扩展提供 TTS；POST /v1/chat/completions，body 含 messages=[{user,""},{assistant,text}] 与 audio={format,voice}。"""

    provider_name = "mimo"
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"tts": "mimo-v2.5-tts"}
    DEFAULT_CONTEXT_TOKENS: ClassVar[dict[str, int]] = {"tts": 8_000}
    SUPPORTS_SYNTH_STREAM = True
    VOICE_DESIGN_GUIDE = """\
关键维度（不需要面面俱到）：
• 性别与年龄：如"二十多岁的年轻女性"、"五十岁的中年男性"
• 音色/质感：如"丝滑醇厚、带磁性"、"清亮柔和"
• 情绪/语气：如"温柔自信"、"慵懒俏皮"
• 语速/节奏：如"语速偏快、像连珠炮"、"缓慢沉稳"
中英文均可，1-4 句即可。避免矛盾特征（如"稚嫩童声+CEO气场"）、音质效果词（混响、回声）和模糊词（"普通的""正常的"）。\
"""
    VOICE_CATALOG: ClassVar[list[dict]] = [
        {
            "id": "mimo_default",
            "label": "默认音色",
            "gender": "neutral",
            "language": "multi",
            "tags": ["默认", "温柔", "自然", "中性"],
            "description": "MiMo 默认音色，自然温和。",
        },
        {
            "id": "冰糖",
            "label": "冰糖",
            "gender": "female",
            "language": "zh",
            "tags": ["冰糖", "温柔", "甜", "女", "中文"],
            "description": "温柔甜美的中文女声。",
        },
        {
            "id": "茉莉",
            "label": "茉莉",
            "gender": "female",
            "language": "zh",
            "tags": ["茉莉", "清亮", "自然", "女", "中文"],
            "description": "清亮自然的中文女声。",
        },
        {
            "id": "苏打",
            "label": "苏打",
            "gender": "male",
            "language": "zh",
            "tags": ["苏打", "清爽", "男", "中文"],
            "description": "清爽干净的中文男声。",
        },
        {
            "id": "白桦",
            "label": "白桦",
            "gender": "male",
            "language": "zh",
            "tags": ["白桦", "沉稳", "低沉", "男", "中文"],
            "description": "沉稳低沉的中文男声。",
        },
        {
            "id": "Mia",
            "label": "Mia",
            "gender": "female",
            "language": "en",
            "tags": ["Mia", "bright", "warm", "female", "english"],
            "description": "Bright warm English female voice.",
        },
        {
            "id": "Chloe",
            "label": "Chloe",
            "gender": "female",
            "language": "en",
            "tags": ["Chloe", "cheerful", "lively", "female", "english"],
            "description": "Cheerful lively English female voice.",
        },
        {
            "id": "Milo",
            "label": "Milo",
            "gender": "male",
            "language": "en",
            "tags": ["Milo", "friendly", "male", "english"],
            "description": "Friendly English male voice.",
        },
        {
            "id": "Dean",
            "label": "Dean",
            "gender": "male",
            "language": "en",
            "tags": ["Dean", "calm", "deep", "male", "english"],
            "description": "Calm deep English male voice.",
        },
    ]

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._client: AsyncOpenAI = get_async_client(config.api_key, config.base_url)

    def raw_client(self) -> AsyncOpenAI | None:
        return self._client

    def _request_parts(
        self,
        text: str,
        voice: str,
        *,
        fmt: str,
        speech_style: SpeechStyle | None = None,
    ) -> tuple[str, list[dict], dict, str]:
        model = _VOICEDESIGN_MODEL if voice.startswith(_VOICEDESIGN_PREFIX) else self.config.model
        instruction = ""
        if (
            speech_style
            and speech_style.provider == "mimo"
            and speech_style_matches(speech_style, self.provider_name, model)
        ):
            direction = speech_style.direction
            instruction = f"角色：{direction.role}\n场景：{direction.scene}\n指导：{direction.guidance}"
            text = styled_speech_text(text, speech_style, provider=self.provider_name, model=model)
        if voice.startswith(_VOICEDESIGN_PREFIX):
            design_prompt = voice[len(_VOICEDESIGN_PREFIX) :]
            if not design_prompt.strip():
                raise ValueError("voice design prompt is empty")
            # 与 JSON-RPC design 路径同长上限；REST /api/media/tts 的 voice 表单字段本无界，否则 voicedesign 模型会照单计费。
            if len(design_prompt) > MAX_VOICE_DESIGN_PROMPT_CHARS:
                raise ValueError(f"prompt exceeds {MAX_VOICE_DESIGN_PROMPT_CHARS} chars")
            messages = [
                {"role": "user", "content": design_prompt + ("\n\n" + instruction if instruction else "")},
                {"role": "assistant", "content": text},
            ]
            return _VOICEDESIGN_MODEL, messages, {"format": fmt, "optimize_text_preview": False}, ""
        chosen_voice = pick_catalog_voice(voice, self.VOICE_CATALOG)
        if voice != chosen_voice:
            logger.info("mimo tts: substituted voice", extra={"requested": voice, "used": chosen_voice})
        messages = [{"role": "user", "content": instruction}, {"role": "assistant", "content": text}]
        return self.config.model, messages, {"format": fmt, "voice": chosen_voice}, chosen_voice

    async def synthesize(
        self,
        text: str,
        *,
        voice: str = "",
        fmt: str = "mp3",
        speed: float | None = None,
        speech_style: SpeechStyle | None = None,
    ) -> TTSResult:
        model, messages, audio_kwargs, chosen_voice = self._request_parts(
            text,
            voice,
            fmt=fmt,
            speech_style=speech_style,
        )
        response = await self._client.chat.completions.create(model=model, messages=messages, audio=audio_kwargs)
        choice = response.choices[0] if response.choices else None
        if not choice or not getattr(choice.message, "audio", None):
            raise RuntimeError("MiMo TTS returned no audio")
        mime = "audio/mpeg" if fmt == "mp3" else f"audio/{fmt}"
        return TTSResult(audio=base64.b64decode(choice.message.audio.data), mime=mime, voice=chosen_voice)

    async def synthesize_stream(
        self,
        text: str,
        *,
        voice: str = "",
        speed: float | None = None,
        speech_style: SpeechStyle | None = None,
    ) -> AsyncIterator[AudioChunk]:
        # voicedesign 的流式官方降级为兼容模式（全部推理完成后一次性返回），仍走同一形态。
        model, messages, audio_kwargs, _ = self._request_parts(text, voice, fmt="pcm16", speech_style=speech_style)
        stream = await self._client.chat.completions.create(
            model=model,
            messages=messages,
            audio=audio_kwargs,
            stream=True,
        )
        async for event in stream:
            if not event.choices:
                continue
            audio = getattr(event.choices[0].delta, "audio", None)
            data = audio.get("data") if isinstance(audio, dict) else getattr(audio, "data", None)
            if not data:
                continue
            yield AudioChunk(base64.b64decode(data), "audio/pcm", sample_rate=_STREAM_SAMPLE_RATE)

    async def design_voice(self, prompt: str, *, preview_text: str = "") -> VoiceDesignResult:
        response = await self._client.chat.completions.create(
            model=_VOICEDESIGN_MODEL,
            messages=[
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": preview_text or "你好，我是你的桌面伙伴。"},
            ],
            audio={"format": "mp3", "optimize_text_preview": True},
        )
        choice = response.choices[0] if response.choices else None
        if not choice or not getattr(choice.message, "audio", None):
            raise RuntimeError("MiMo voice design returned no audio")
        return VoiceDesignResult(
            voice_id=f"{_VOICEDESIGN_PREFIX}{prompt}",
            trial_audio=base64.b64decode(choice.message.audio.data),
            trial_audio_mime="audio/mpeg",
            provider=self.provider_name,
        )
