import base64
from collections.abc import AsyncGenerator
from typing import ClassVar

from infrastructure.ai.providers.base import (
    ProviderCapabilityError,
    ProviderConfig,
    ProviderError,
    TTSProvider,
    TTSResult,
    TTSStreamChunk,
    VoiceDesignResult,
    VoiceReferenceAudio,
    pick_catalog_voice,
)
from infrastructure.ai.providers.http import get_async_client
from infrastructure.ai.providers.speech_style import speech_style_matches, styled_speech_text
from infrastructure.ai.speech_contracts import MiMoSpeechStyle, SpeechStyle
from infrastructure.constants import MAX_VOICE_DESIGN_PROMPT_CHARS, VOICE_REFERENCE_MAX_BASE64_BYTES

VOICEDESIGN_MODEL = "mimo-v2.5-tts-voicedesign"
VOICECLONE_MODEL = "mimo-v2.5-tts-voiceclone"
BUILTIN_VOICE_MODEL = "mimo-v2.5-tts"
# 流式合成固定 24 kHz PCM16LE 单声道，与通话播放采样率一致。
PCM_FORMAT = "pcm16"
PCM_MIME = "audio/pcm;rate=24000"


def _delta_audio(delta: object) -> str:
    """读取流式分片里的 Base64 音频；SDK 可能以 dict 或对象承载未建模字段。"""
    audio = getattr(delta, "audio", None)
    if audio is None:
        extra = getattr(delta, "model_extra", None)
        audio = extra.get("audio") if isinstance(extra, dict) else None
    data = audio.get("data") if isinstance(audio, dict) else getattr(audio, "data", None)
    return data if isinstance(data, str) and data else ""


class MiMoTTSProvider(TTSProvider):
    """通过 MiMo 在 Chat Completions 上的 audio={...} 扩展提供 TTS；POST /v1/chat/completions，body 含 messages=[{user,""},{assistant,text}] 与 audio={format,voice}。"""

    provider_name = "mimo"
    DEFAULT_BASE_URL: ClassVar[str] = "https://token-plan-cn.xiaomimimo.com/v1"
    # 内置音色模型支持 24 kHz PCM 增量输出；设计/克隆音色仍走整段合成。
    supports_streaming: ClassVar[bool] = True
    VOICE_DESIGN_MODEL: ClassVar[str | None] = VOICEDESIGN_MODEL
    VOICE_CLONE_MODEL: ClassVar[str | None] = VOICECLONE_MODEL
    BUILTIN_VOICE_MODEL: ClassVar[str | None] = BUILTIN_VOICE_MODEL
    VOICE_DESIGN_GUIDE = """\
用1至4句刻画声音，选取鲜明且协调的特征：性别与年龄感、音色质感、习惯语气、语速和节奏；需要时补充口音。
描述具体的声音特点，例如声线低沉醇厚、咬字轻巧、语气温暖或节奏从容。不写混响、回声等后期音效。\
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
        self._client = get_async_client(config.api_key, config.base_url)

    @classmethod
    def supports_streaming_config(cls, config: ProviderConfig) -> bool:
        return config.model == BUILTIN_VOICE_MODEL

    def _request_parts(
        self,
        text: str,
        voice: str,
        speech_style: SpeechStyle | None,
        *,
        audio_format: str = "mp3",
        design_prompt: str = "",
        reference_audio: VoiceReferenceAudio | None = None,
    ) -> tuple[str, list[dict], dict, str]:
        model = self.config.model
        if design_prompt and reference_audio is not None:
            raise ValueError("Voice design and reference audio cannot be combined")
        required_model = BUILTIN_VOICE_MODEL
        if reference_audio is not None:
            required_model = VOICECLONE_MODEL
        elif design_prompt:
            required_model = VOICEDESIGN_MODEL
        if required_model != model:
            raise ValueError(f"MiMo 当前音色需要配置 {required_model} 模型卡片")
        instruction = ""
        if isinstance(speech_style, MiMoSpeechStyle) and speech_style_matches(speech_style, self.provider_name, model):
            instruction = speech_style.instruction or ""
            text = styled_speech_text(text, speech_style)
        if reference_audio is not None:
            if reference_audio.mime not in {"audio/wav", "audio/mpeg", "audio/mp3"}:
                raise ValueError("MiMo 声音克隆只支持 WAV 或 MP3 参考音频")
            if not reference_audio.audio:
                raise ValueError("MiMo 声音克隆参考音频为空")
            encoded = base64.b64encode(reference_audio.audio).decode("ascii")
            if len(encoded) > VOICE_REFERENCE_MAX_BASE64_BYTES:
                raise ValueError("MiMo 声音克隆参考音频 Base64 不能超过 10 MB")
            messages = [{"role": "user", "content": instruction}, {"role": "assistant", "content": text}]
            return (
                model,
                messages,
                {"format": audio_format, "voice": f"data:{reference_audio.mime};base64,{encoded}"},
                "",
            )
        if design_prompt:
            if not design_prompt.strip():
                raise ValueError("voice design prompt is empty")
            if len(design_prompt) > MAX_VOICE_DESIGN_PROMPT_CHARS:
                raise ValueError(f"prompt exceeds {MAX_VOICE_DESIGN_PROMPT_CHARS} chars")
            messages = [
                {"role": "user", "content": design_prompt + ("\n\n" + instruction if instruction else "")},
                {"role": "assistant", "content": text},
            ]
            return model, messages, {"format": audio_format, "optimize_text_preview": False}, ""
        chosen_voice = pick_catalog_voice(voice, self.VOICE_CATALOG, provider=self.provider_name)
        messages = [{"role": "user", "content": instruction}, {"role": "assistant", "content": text}]
        return self.config.model, messages, {"format": audio_format, "voice": chosen_voice}, chosen_voice

    async def synthesize(self, text: str, *, voice: str, speech_style: SpeechStyle | None) -> TTSResult:
        model, messages, audio_kwargs, chosen_voice = self._request_parts(text, voice, speech_style)
        return await self._synthesize_request(model, messages, audio_kwargs, chosen_voice)

    async def synthesize_designed(
        self, text: str, *, prompt: str, voice: str, speech_style: SpeechStyle | None
    ) -> TTSResult:
        if not prompt.strip():
            raise ValueError("voice design prompt is empty")
        model, messages, audio_kwargs, chosen_voice = self._request_parts(text, "", speech_style, design_prompt=prompt)
        return await self._synthesize_request(model, messages, audio_kwargs, chosen_voice)

    async def synthesize_with_reference(
        self, text: str, *, reference_audio: VoiceReferenceAudio, speech_style: SpeechStyle | None
    ) -> TTSResult:
        model, messages, audio_kwargs, chosen_voice = self._request_parts(
            text, "", speech_style, reference_audio=reference_audio
        )
        return await self._synthesize_request(model, messages, audio_kwargs, chosen_voice)

    async def _synthesize_request(
        self, model: str, messages: list[dict], audio_kwargs: dict, chosen_voice: str
    ) -> TTSResult:
        response = await self._client.chat.completions.create(model=model, messages=messages, audio=audio_kwargs)
        choice = response.choices[0] if response.choices else None
        if choice is not None and choice.finish_reason != "stop":
            raise RuntimeError(f"MiMo TTS response did not complete: {choice.finish_reason}")
        if not choice or not getattr(choice.message, "audio", None):
            raise RuntimeError("MiMo TTS returned no audio")
        return TTSResult(audio=base64.b64decode(choice.message.audio.data), mime="audio/mpeg", voice=chosen_voice)

    async def synthesize_stream(
        self,
        text: str,
        *,
        voice: str,
        speech_style: SpeechStyle | None,
        design_prompt: str = "",
        reference_audio: VoiceReferenceAudio | None = None,
    ) -> AsyncGenerator[TTSStreamChunk]:
        if reference_audio is not None or design_prompt:
            raise ProviderCapabilityError("MiMo 流式合成只支持内置音色")
        if not text.strip():
            raise ValueError("MiMo streaming TTS text is empty")
        model, messages, audio_kwargs, chosen_voice = self._request_parts(
            text, voice, speech_style, audio_format=PCM_FORMAT
        )
        stream = await self._client.chat.completions.create(
            model=model, messages=messages, audio=audio_kwargs, stream=True
        )
        pending = b""
        emitted = False
        try:
            async for chunk in stream:
                choice = chunk.choices[0] if chunk.choices else None
                if choice is None:
                    continue
                if choice.finish_reason not in (None, "stop"):
                    raise ProviderError(f"MiMo streaming TTS did not complete: {choice.finish_reason}", status_code=502)
                encoded = _delta_audio(choice.delta)
                if not encoded:
                    continue
                try:
                    pending += base64.b64decode(encoded)
                except (TypeError, ValueError) as exc:
                    raise ProviderError("MiMo streaming TTS returned invalid audio", status_code=502) from exc
                # 分片边界可能落在采样中间，保留余量让每块都是完整 PCM16。
                cut = len(pending) - len(pending) % 2
                if not cut:
                    continue
                emitted = True
                yield TTSStreamChunk(audio=pending[:cut], mime=PCM_MIME, voice=chosen_voice)
                pending = pending[cut:]
        finally:
            await stream.close()
        if not emitted:
            raise ProviderCapabilityError("MiMo streaming TTS returned no audio")
        yield TTSStreamChunk(audio=b"", mime=PCM_MIME, final=True, voice=chosen_voice)

    async def design_voice(self, prompt: str, *, preview_text: str) -> VoiceDesignResult:
        result = await self.synthesize_designed(preview_text, prompt=prompt, voice="", speech_style=None)
        return VoiceDesignResult(
            voice_id="", trial_audio=result.audio, trial_audio_mime=result.mime, provider=self.provider_name
        )
