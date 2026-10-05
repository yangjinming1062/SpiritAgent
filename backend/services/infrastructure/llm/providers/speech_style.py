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
MiMo accepts open-ended natural-language delivery controls.
- styles: zero to four overall labels that genuinely affect the whole utterance. Useful categories include emotion
  (平静/开心/委屈/释然), tone (温柔/俏皮/严肃/慵懒), voice quality (清亮/磁性/气声), or a supported dialect.
  These are examples, not a whitelist; do not add a style merely to fill the field.
- cues: local audible events or delivery changes, such as 轻笑, 叹气, 吸气, 小声, 语速加快, 苦笑, 哽咽,
  or 咳嗽. Use a precise custom description when needed. A cue must be justified by the adjacent words; it is not
  a place for visual gestures, inner thoughts, or invented events.
- direction is required. role contains only voice-relevant persona traits and the listener relationship (max 500
  characters); scene contains the situation established by this exchange and your attitude, without invented facts
  (max 500); guidance contains the few important choices of pace, pauses, emphasis, and emotional progression (max
  1500). Keep all three concise.
Preserve the selected voice and one-speaker persona. styles, cues, and direction must agree without repeating the same
instruction in every field or escalating drama beyond the dialogue. Do not include brackets in style or cue values.
"""
# 句内标记在 styled_speech_text 中插到 before 短语之前，校验只接受本气泡台词中唯一出现的短语。
_CUE_RULE = (
    "Each cue is an object {before, tag}: the tag is performed immediately before its before phrase, which must occur "
    "exactly once in this bubble's text. Use at most eight cues, and never add dialogue merely to create an anchor.\n"
)


def _choices(mapping: dict[str, str]) -> str:
    """列出可填写的键；括号内是含义，校验只接受键本身。"""
    return ", ".join(f"{key} ({meaning})" for key, meaning in mapping.items())


def speech_style_guidance(provider: str, model: str, *, language: str) -> str:
    if provider == "mimo":
        example: dict = {
            "styles": [],
            "direction": {
                "role": "configured persona and voice",
                "scene": "the current exchange",
                "guidance": "natural delivery; add emphasis only where the words support it",
            },
            "cues": [],
        }
        capabilities = _MIMO_GUIDANCE + _CUE_RULE
        capabilities += (
            "Singing styles: 唱歌/sing/singing (put this FIRST in styles; Chinese lyrics work best).\n"
            if model == "mimo-v2.5-tts"
            else "This model does not support singing; do not request singing in styles, cues or director guidance.\n"
        )
    elif provider == "minimax":
        emotions = dict(_MINIMAX_EMOTIONS)
        if model in _MINIMAX_EXTENDED_EMOTION_MODELS:
            emotions.update(fluent="生动", whisper="低语")
        cues_supported = model in _MINIMAX_CUE_MODELS
        example = {
            "emotion": None,
            "speed": 1,
            "cues": [],
            "pauses": [],
        }
        capabilities = (
            f"emotion: null for automatic delivery, or exactly one of these keys: {_choices(emotions)}.\n"
            + (
                f"cues: optional inline sounds; tag must be exactly one of these keys: {_choices(_MINIMAX_CUES)}. "
                + _CUE_RULE
                if cues_supported
                else "cues: this model has no inline sounds; keep cues empty.\n"
            )
            + "speed: 0.5 to 2, where 1 is normal. pauses: optional {before, seconds} objects, 0.01 to 99.99 seconds with "
            "at most two decimal places; each pause is inserted immediately before its before phrase, which must occur "
            "exactly once and follow some spoken words, never the opening word. Pauses cannot share a position; omit them "
            "when punctuation already separates the words. This model has no free-form style or director field.\n"
        )
    else:
        return ""
    return (
        "\n## Speech performance for voice bubbles\n"
        "Each voice bubble nests its speech object as shown; text bubbles have no speech. Do not include provider or "
        "model identifiers. Choose delivery for this bubble's words and context.\n"
        + json.dumps(
            [
                {"type": "voice", "text": text, "speech": example}
                for text in (
                    ("我听着呢。", "你慢慢说。") if language == "zh" else ("I'm listening.", "Take your time.")
                )
            ],
            ensure_ascii=False,
        )
        + "\n"
        + capabilities
        + "Use natural delivery by default. All direction stays in speech; text contains only the words actually spoken.\n"
    )


def speech_performance_schema(provider: str, model: str) -> dict:
    """从演绎校验模型生成本轮输入结构，供应商绑定由服务端完成。"""
    style_model = {"mimo": MiMoSpeechStyle, "minimax": MiniMaxSpeechStyle}[provider]
    schema = style_model.model_json_schema()
    schema["minProperties"] = 1
    for field in ("provider", "model"):
        schema["properties"].pop(field)
        schema["required"].remove(field)
    if provider == "minimax":
        emotions = list(_MINIMAX_EMOTIONS)
        if model in _MINIMAX_EXTENDED_EMOTION_MODELS:
            emotions.extend(("fluent", "whisper"))
        for variant in schema["properties"]["emotion"]["anyOf"]:
            if variant.get("type") == "string":
                variant["enum"] = emotions
        if model in _MINIMAX_CUE_MODELS:
            schema["$defs"]["SpeechCue"]["properties"]["tag"]["enum"] = list(_MINIMAX_CUES)
        else:
            schema["properties"]["cues"]["maxItems"] = 0
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
            tag.strip().lower() in {"唱歌", "sing", "singing"}
            for tag in [*style.styles, *(cue.tag for cue in style.cues)]
        )
    ):
        reject(("styles",), "Singing is not supported by this speech model")
    if style.provider == "minimax":
        if style.emotion in {"fluent", "whisper"} and model not in _MINIMAX_EXTENDED_EMOTION_MODELS:
            reject(("emotion",), f"Emotion must be null or one of: {', '.join(_MINIMAX_EMOTIONS)}")
        if style.cues and model not in _MINIMAX_CUE_MODELS:
            reject(("cues",), "This speech model requires an empty cues array")
        elif model in _MINIMAX_CUE_MODELS:
            for index, cue in enumerate(style.cues):
                if cue.tag not in _MINIMAX_CUES:
                    reject(("cues", index, "tag"), f"Cue tag must be one of: {', '.join(_MINIMAX_CUES)}")
    if errors:
        raise ValidationError.from_exception_data("SpeechStyle", errors)


def speech_style_matches(style: SpeechStyle, provider: str, model: str) -> bool:
    try:
        validate_speech_style(style, provider, model)
    except ValidationError:
        return False
    return True


def styled_speech_text(text: str, style: SpeechStyle) -> str:
    """把已匹配当前供应商与模型的演绎标注嵌入朗读文本。"""
    insertions: dict[int, str] = {}
    for cue in style.cues:
        if text.count(cue.before) == 1:
            position = text.index(cue.before)
            tag = f"[{cue.tag}]" if style.provider == "mimo" else f"({cue.tag})"
            insertions[position] = insertions.get(position, "") + tag
    if style.provider == "minimax":
        pause_positions: set[int] = set()
        for pause in style.pauses:
            if (
                text.count(pause.before) == 1
                and (position := text.index(pause.before)) > 0
                and text[:position].strip()
                and position not in pause_positions
            ):
                insertions[position] = insertions.get(position, "") + f"<#{pause.seconds:g}#>"
                pause_positions.add(position)
    for position in sorted(insertions, reverse=True):
        text = text[:position] + insertions[position] + text[position:]
    if style.provider == "mimo" and style.styles:
        text = f"({' '.join(style.styles)}){text}"
    return text
