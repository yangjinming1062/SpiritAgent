import json

from components import resolve_prompt_text
from modules.media import MiMoSpeechStyle, MiniMaxSpeechStyle, SpeechStyle
from prompts.speech import MIMO_SPEECH_GUIDANCES, SPEECH_PERFORMANCE_GUIDANCES, SPEECH_SEGMENT_GUIDANCES
from pydantic import ValidationError
from pydantic_core import InitErrorDetails

_MINIMAX_CUES = {
    "laughs": "笑声",
    "chuckle": "轻笑",
    "coughs": "咳嗽",
    "clear-throat": "清嗓子",
    "groans": "呻吟",
    "breath": "正常换气",
    "pant": "喘气",
    "inhale": "吸气",
    "exhale": "呼气",
    "gasps": "倒吸气",
    "sniffs": "吸鼻子",
    "sighs": "叹气",
    "snorts": "喷鼻息",
    "burps": "打嗝",
    "lip-smacking": "咂嘴",
    "humming": "哼唱",
    "hissing": "嘶嘶声",
    "emm": "嗯",
    "sneezes": "喷嚏",
}
_MINIMAX_CUE_MODELS = {"speech-2.8-hd", "speech-2.8-turbo"}
_MINIMAX_EXTENDED_EMOTION_MODELS = {"speech-2.6-hd", "speech-2.6-turbo"}
_MINIMAX_EMOTIONS = {
    "happy": "高兴",
    "sad": "悲伤",
    "angry": "愤怒",
    "fearful": "害怕",
    "disgusted": "厌恶",
    "surprised": "惊讶",
    "calm": "中性",
}


def _choices(mapping: dict[str, str]) -> str:
    """列出可填写的键；括号内是含义，校验只接受键本身。"""
    return ", ".join(f"{key} ({meaning})" for key, meaning in mapping.items())


def speech_style_guidance(provider: str, model: str, *, language: str) -> str:
    zh = language == "zh"
    plain = "你先忙，我等你。" if zh else "Go ahead, I'll wait."
    confident = "这件事交给我。" if zh else "I'll take care of it."
    setup = "好吧，这回算你赢，" if zh else "Fine, you win this time, "
    ending = "下次可不一定。" if zh else "but maybe not next time."
    if provider == "mimo":
        examples = [
            {"type": "voice", "text": plain, "speech": {"instruction": None, "styles": [], "segments": []}},
            {
                "type": "voice",
                "text": confident,
                "speech": {
                    "instruction": (
                        "说得干脆、可靠，像接下一个熟悉的任务。"
                        if zh
                        else "Sound assured and matter-of-fact, like accepting a familiar task."
                    ),
                    "styles": [],
                    "segments": [],
                },
            },
            {
                "type": "voice",
                "text": setup + ending,
                "speech": {
                    "instruction": None,
                    "styles": ["俏皮"],
                    "segments": [{"text": setup, "tag": None}, {"text": ending, "tag": "轻笑"}],
                },
            },
        ]
        capabilities = resolve_prompt_text(MIMO_SPEECH_GUIDANCES, language)
        if model == "mimo-v2.5-tts":
            capabilities += (
                "需要唱歌时，将 唱歌/sing/singing 放在 styles 的第一项；中文歌词效果更好。\n"
                if zh
                else "For singing, put 唱歌/sing/singing FIRST in styles; Chinese lyrics work best.\n"
            )
        else:
            capabilities += (
                "本模型不支持唱歌，instruction、styles 与 tag 都不要求唱歌。\n"
                if zh
                else "This model does not support singing; do not request it in instruction, styles, or tags.\n"
            )
    elif provider == "minimax":
        emotions = dict(_MINIMAX_EMOTIONS)
        if model in _MINIMAX_EXTENDED_EMOTION_MODELS:
            emotions.update(fluent="生动", whisper="低语")
        cues_supported = model in _MINIMAX_CUE_MODELS

        examples = [{"type": "voice", "text": plain, "speech": {"emotion": None, "speed": 1, "segments": []}}]
        capabilities = (
            f"emotion：null 表示自动演绎，也可选一个准确的键：{_choices(emotions)}。\n"
            if zh
            else f"emotion: null for automatic delivery, or exactly one of these keys: {_choices(emotions)}.\n"
        )
        if cues_supported:
            capabilities += (
                f"segment.tag 只能选这些键：{_choices(_MINIMAX_CUES)}。\n"
                if zh
                else f"Each segment tag must be exactly one of these keys: {_choices(_MINIMAX_CUES)}.\n"
            )
            examples.append(
                {
                    "type": "voice",
                    "text": setup + ending,
                    "speech": {
                        "emotion": None,
                        "speed": 1,
                        "segments": [{"text": setup, "tag": None}, {"text": ending, "tag": "chuckle"}],
                    },
                },
            )
        else:
            capabilities += (
                "本模型没有段内声音标签，segments 不填写 tag。\n"
                if zh
                else "This model has no inline sound tags; omit tag in segments.\n"
            )
        capabilities += (
            "speed：0.5 到 2，1 是正常速度。segment.pause 可用 0.01 到 99.99 秒，最多两位小数，"
            "在该段文字前停顿；须已有说出的词，不能放在开头，标点足够时不额外添加。"
            "本模型不支持 instruction 或 styles。\n"
            if zh
            else "speed: 0.5 to 2, where 1 is normal. A segment may set pause: 0.01 to 99.99 seconds with at most two "
            "decimal places, performed before that segment's words after some spoken words, never the opening word; "
            "omit it when punctuation already separates the words. This model has no instruction or styles field.\n"
        )
    else:
        return ""
    return (
        resolve_prompt_text(SPEECH_PERFORMANCE_GUIDANCES, language)
        + "\n".join(json.dumps(example, ensure_ascii=False) for example in examples)
        + "\n"
        + capabilities
        + resolve_prompt_text(SPEECH_SEGMENT_GUIDANCES, language)
    )


def speech_performance_schema(provider: str, model: str) -> dict:
    """从演绎校验模型生成本轮输入结构，供应商绑定由服务端完成。"""
    style_model = {"mimo": MiMoSpeechStyle, "minimax": MiniMaxSpeechStyle}[provider]
    schema = style_model.model_json_schema()
    for field in ("provider", "model"):
        schema["properties"].pop(field)
        schema["required"].remove(field)
    segment = schema["$defs"]["SpeechSegment"]
    if provider == "mimo":
        segment["properties"].pop("pause", None)
    else:
        emotions = list(_MINIMAX_EMOTIONS)
        if model in _MINIMAX_EXTENDED_EMOTION_MODELS:
            emotions.extend(("fluent", "whisper"))
        for variant in schema["properties"]["emotion"]["anyOf"]:
            if variant.get("type") == "string":
                variant["enum"] = emotions
        if model in _MINIMAX_CUE_MODELS:
            for variant in segment["properties"]["tag"]["anyOf"]:
                if variant.get("type") == "string":
                    variant["enum"] = list(_MINIMAX_CUES)
        else:
            segment["properties"].pop("tag", None)
    return schema


def validate_speech_style(style: SpeechStyle, provider: str, model: str) -> None:
    errors: list[InitErrorDetails] = []

    def reject(path: tuple[str | int, ...], message: str) -> None:
        errors.append({"type": "value_error", "loc": (provider, *path), "ctx": {"error": ValueError(message)}})

    if style.provider != provider or style.model != model:
        reject((), "Speech performance must use the configured provider and model")
    if (
        style.provider == "mimo"
        and model != "mimo-v2.5-tts"
        and any(
            value.strip().lower() in {"唱歌", "sing", "singing"}
            for value in [*style.styles, *(s.tag for s in style.segments if s.tag)]
        )
    ):
        reject(("styles",), "Singing is not supported by this speech model")
    if style.provider == "mimo" and any(segment.pause is not None for segment in style.segments):
        reject(("segments",), "MiMo delivery has no timed pause; describe pacing in instruction instead")
    if style.provider == "minimax":
        if style.emotion in {"fluent", "whisper"} and model not in _MINIMAX_EXTENDED_EMOTION_MODELS:
            reject(("emotion",), f"Emotion must be null or one of: {', '.join(_MINIMAX_EMOTIONS)}")
        if model in _MINIMAX_CUE_MODELS:
            for index, segment in enumerate(style.segments):
                if segment.tag is not None and segment.tag not in _MINIMAX_CUES:
                    reject(("segments", index, "tag"), f"Segment tag must be one of: {', '.join(_MINIMAX_CUES)}")
        elif any(segment.tag is not None for segment in style.segments):
            reject(("segments",), "This speech model has no inline sound tags")
    if errors:
        raise ValidationError.from_exception_data("SpeechStyle", errors)


def speech_style_matches(style: SpeechStyle, provider: str, model: str) -> bool:
    try:
        validate_speech_style(style, provider, model)
    except ValidationError:
        return False
    return True


def styled_speech_text(text: str, style: SpeechStyle) -> str:
    """把已匹配当前供应商与模型的演绎标记嵌入朗读文本。"""
    if style.segments and "".join(segment.text for segment in style.segments) != text:
        raise ValueError("Segment texts must concatenate to exactly the spoken text")
    parts: list[str] = []
    for segment in style.segments:
        if style.provider == "mimo":
            if segment.tag:
                parts.append(f"[{segment.tag}]")
        elif segment.tag:
            parts.append(f"({segment.tag})")
        if segment.pause is not None:
            parts.append(f"<#{segment.pause:g}#>")
        parts.append(segment.text)
    rendered = "".join(parts)
    if rendered:
        text = rendered
    if style.provider == "mimo" and style.styles:
        text = f"({' '.join(style.styles)}){text}"
    return text
