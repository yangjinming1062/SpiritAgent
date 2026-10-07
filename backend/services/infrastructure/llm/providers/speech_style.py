import json

from modules.media import MiMoSpeechStyle, MiniMaxSpeechStyle, SpeechStyle
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

_MIMO_GUIDANCE = """
MiMo accepts two complementary delivery controls. Write them per voice bubble, from where the conversation stands in
this turn: a copied direction makes consecutive lines sound identical, so re-derive it as the exchange moves on.
- direction is required and is sent as one director instruction covering character, scene, and acting guidance:
  role contains only voice-relevant persona traits and the listener relationship (max 500 characters); scene contains
  the situation established by this exchange and your attitude, without invented facts (max 500); guidance contains
  this sentence's delivery choices - pace, pauses, breath, and emphasis on actual words of this bubble's text (max
  1500). Match the line's length: a short line needs one or two concrete choices, not an arc that its words cannot
  carry. Use brief phrases for each; direction already conveys tone and scene, so styles and segment tags are optional.
- styles: zero to four labels prefixed to the whole utterance that genuinely affect it, such as an emotion
  (开心/委屈/释然), a tone (温柔/俏皮/慵懒), voice quality (清亮/磁性/气声), or a supported dialect. These are
  examples, not a whitelist; choose them from this moment's mood and do not attach a label merely to fill the field.
"""
# 句内标记用 segments 表达位置：各段 text 按序拼接必须逐字还原本气泡台词，标记放在发生位置所属的分段上。
_SEGMENT_RULE = (
    "Inline positions are expressed through segments: split this bubble's spoken sentence into consecutive segments "
    "whose text values concatenate, in order, to exactly this bubble's text. Set tag on a segment only when a control "
    "starts at that point and leave it null on segments that need none; an initial empty-text segment may carry the "
    "opening tag. A tag must be justified by the adjacent words, marking an audible or delivery change such as 轻笑, "
    "叹气, 吸气, 深呼吸, 小声, 微笑, 哽咽, 鼻音, or 语速加快, never a visual gesture, inner thought, invented "
    "event, or a full delivery description; two to six characters are enough. Never change the dialogue to create a "
    "segment boundary, and use at most twelve segments.\n"
)


def _choices(mapping: dict[str, str]) -> str:
    """列出可填写的键；括号内是含义，校验只接受键本身。"""
    return ", ".join(f"{key} ({meaning})" for key, meaning in mapping.items())


def speech_style_guidance(provider: str, model: str, *, language: str) -> str:
    examples = (
        (("我听着呢。", "末尾“呢”字轻轻拖长，带一点笑意，语速从容"), ("你慢慢说。", "“慢慢”二字放轻放缓，像在安抚"))
        if language == "zh"
        else (
            ("I'm listening.", "hold the ending lightly, with a faint smile in the voice, unhurried"),
            ("Take your time.", "soften and slow the words, reassuring"),
        )
    )
    if provider == "mimo":

        def example(text: str, guidance: str) -> dict:
            return {
                "styles": [],
                "direction": {
                    "role": "configured persona and voice",
                    "scene": "the current exchange",
                    "guidance": guidance,
                },
                "segments": [{"text": text, "tag": None}],
            }

        capabilities = _MIMO_GUIDANCE + _SEGMENT_RULE
        capabilities += (
            "Singing styles: 唱歌/sing/singing (put this FIRST in styles; Chinese lyrics work best).\n"
            if model == "mimo-v2.5-tts"
            else "This model does not support singing; do not request singing in styles, segments, or director guidance.\n"
        )
    elif provider == "minimax":
        emotions = dict(_MINIMAX_EMOTIONS)
        if model in _MINIMAX_EXTENDED_EMOTION_MODELS:
            emotions.update(fluent="生动", whisper="低语")
        cues_supported = model in _MINIMAX_CUE_MODELS

        def example(text: str, _guidance: str) -> dict:
            segment: dict = {"text": text, "pause": None}
            if cues_supported:
                segment["tag"] = None
            return {"emotion": None, "speed": 1, "segments": [segment]}

        capabilities = f"emotion: null for automatic delivery, or exactly one of these keys: {_choices(emotions)}.\n"
        if cues_supported:
            capabilities += f"Each segment tag must be exactly one of these keys: {_choices(_MINIMAX_CUES)}.\n"
        capabilities += (
            _SEGMENT_RULE
            if cues_supported
            else ("This model has no inline sound tags; every segment sets text only.\n")
        )
        capabilities += (
            "speed: 0.5 to 2, where 1 is normal. A segment may set pause: 0.01 to 99.99 seconds with at most two "
            "decimal places, performed before that segment's words after some spoken words, never the opening word; "
            "omit it when punctuation already separates the words. This model has no free-form style or director "
            "field.\n"
        )
    else:
        return ""
    return (
        "\n## Speech performance for voice bubbles\n"
        "Each voice bubble nests its speech object as shown; text bubbles have no speech. Do not include provider or "
        "model identifiers. Write this bubble's spoken sentence first, then its matching performance. "
        "Use only controls that contribute to the intended delivery; optional markers need not appear.\n"
        + json.dumps(
            {
                "kind": "dialogue",
                "bubbles": [
                    {"type": "voice", "text": text, "speech": example(text, guidance)} for text, guidance in examples
                ],
            },
            ensure_ascii=False,
        )
        + "\n"
        + capabilities
        + "Use natural delivery by default; performance stays in speech, and text contains only the words actually "
        "spoken.\n"
    )


def speech_performance_schema(provider: str, model: str) -> dict:
    """从演绎校验模型生成本轮输入结构，供应商绑定由服务端完成。"""
    style_model = {"mimo": MiMoSpeechStyle, "minimax": MiniMaxSpeechStyle}[provider]
    schema = style_model.model_json_schema()
    schema["minProperties"] = 1
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
        reject(("segments",), "MiMo delivery has no timed pause; describe pacing in direction guidance instead")
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
