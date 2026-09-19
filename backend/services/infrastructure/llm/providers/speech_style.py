import json

from modules.media import SpeechStyle

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


def speech_style_guidance(provider: str, model: str) -> str:
    example: dict = {}
    if provider == "mimo":
        example.update(
            styles=[],
            direction={
                "role": "configured persona and voice",
                "scene": "the current exchange",
                "guidance": "natural delivery; add emphasis only where the words support it",
            },
            cues=[],
        )
        capabilities = _MIMO_GUIDANCE
        capabilities += (
            "Singing styles: 唱歌/sing/singing (put this FIRST in styles; Chinese lyrics work best).\n"
            if model == "mimo-v2.5-tts"
            else "This model does not support singing; do not request singing in styles, cues or director guidance.\n"
        )
    elif provider == "minimax":
        example.update(emotion=None, speed=1, cues=[], pauses=[])
        emotions = dict(_MINIMAX_EMOTIONS)
        if model in _MINIMAX_EXTENDED_EMOTION_MODELS:
            emotions.update(fluent="生动", whisper="低语")
        cues = _MINIMAX_CUES if model in _MINIMAX_CUE_MODELS else {}
        capabilities = (
            f"MiniMax emotion values for this model: {json.dumps(emotions, ensure_ascii=False)}. emotion may be null for automatic delivery.\n"
            f"Full inline cue tag catalogue for this model: {json.dumps(cues, ensure_ascii=False)}. Use ONLY these exact tag values.\n"
            "speed: 0.5 to 2, where 1 is normal. pauses: optional {before, seconds}, 0.01 to 99.99 seconds, at most two decimal places. "
            "Pauses must be between spoken words, never at the start/end or consecutive. This model has no free-form style or director field.\n"
        )
    else:
        return ""
    return (
        "\n## Speech performance for each voice bubble\n"
        "The speech object of each voice bubble uses this shape; text bubbles have no speech object. "
        "Do not include provider or model identifiers. Choose delivery for this bubble's words and context:\n"
        + json.dumps(example, ensure_ascii=False)
        + "\n"
        + capabilities
        + "Use natural delivery by default and at most eight cues. Each cue is {before, tag}; before must be an "
        "exact substring occurring once in this bubble's text. Never add dialogue merely to create an anchor. "
        "All direction stays in speech; text contains only actual spoken words.\n"
    )


def speech_style_matches(style: SpeechStyle, provider: str, model: str) -> bool:
    if style.provider != provider or style.model != model:
        return False
    if (
        style.provider == "mimo"
        and model != "mimo-v2.5-tts"
        and any(
            tag.strip().lower() in {"唱歌", "sing", "singing"}
            for tag in [*style.styles, *(cue.tag for cue in style.cues)]
        )
    ):
        return False
    if style.provider == "minimax":
        if style.emotion in {"fluent", "whisper"} and model not in _MINIMAX_EXTENDED_EMOTION_MODELS:
            return False
        if style.cues and (model not in _MINIMAX_CUE_MODELS or any(cue.tag not in _MINIMAX_CUES for cue in style.cues)):
            return False
    return True


def styled_speech_text(text: str, style: SpeechStyle | None, *, provider: str, model: str) -> str:
    if style is None or not speech_style_matches(style, provider, model):
        return text
    insertions: dict[int, str] = {}
    for cue in style.cues:
        if text.count(cue.before) == 1:
            position = text.index(cue.before)
            tag = f"[{cue.tag}]" if provider == "mimo" else f"({cue.tag})"
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
