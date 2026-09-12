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
MiMo supports open-ended style and inline audio tags, including custom natural-language tags.
styles: overall style labels, combined at the beginning of the spoken text. Recommended labels:
- 基础情绪：开心/悲伤/愤怒/恐惧/惊讶/兴奋/委屈/平静/冷漠
- 复合情绪：怅然/欣慰/无奈/愧疚/释然/嫉妒/厌倦/忐忑/动情
- 整体语调：温柔/高冷/活泼/严肃/慵懒/俏皮/深沉/干练/凌厉
- 音色定位：磁性/醇厚/清亮/空灵/稚嫩/苍老/甜美/沙哑/醇雅
- 人设腔调：夹子音/御姐音/正太音/大叔音/台湾腔
- 方言：东北话/四川话/河南话/粤语；角色扮演：孙悟空/林黛玉
cues: inline audio instructions. Recommended tag values:
- 语速与节奏：吸气/深呼吸/叹气/长叹一口气/喘息/屏息
- 情绪状态：紧张/害怕/激动/疲惫/委屈/撒娇/心虚/震惊/不耐烦
- 语音特征：颤抖/声音颤抖/变调/破音/鼻音/气声/沙哑
- 哭笑表达：笑/轻笑/大笑/冷笑/抽泣/呜咽/哽咽/嚎啕大哭
- Further documented examples: 语速加快/碎碎念/小声/沉默片刻/苦笑/咳嗽/提高音量喊话.
These are examples, not a closed whitelist; use custom precise delivery descriptions when useful.
direction: REQUIRED director mode, with three natural-language fields (Chinese or English):
- role: this companion's identity, personality, speaking habits and relationship to the user.
- scene: what is happening now, whom you address, the conversational context and emotional position.
- guidance: concrete acting direction for speed, pauses, breath, emphasis, resonance, timbre and emotional progression.
Write concise, contextual direction alongside the reply; avoid boilerplate. Describe the vocal performance,
not imaginary real-world actions. Preserve the selected voice and persona; do not invent a different speaker.
Overall styles, inline cues and director guidance must agree. Do not include brackets around styles or cue tags.
"""


def speech_style_guidance(provider: str, model: str) -> str:
    example: dict = {"provider": provider, "model": model}
    if provider == "mimo":
        example.update(
            styles=["温柔"],
            direction={
                "role": "your character",
                "scene": "this conversation",
                "guidance": "contextual vocal direction",
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
        example.update(emotion="calm", speed=1, cues=[], pauses=[])
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
        "\n## Hidden speech delivery for the selected voice\n"
        "Precede each response with exactly one hidden header, using this provider-specific schema:\n"
        f"<speech_style>{json.dumps(example, ensure_ascii=False)}</speech_style>\n"
        + capabilities
        + "Choose delivery together with your words, using persona, relationship and current context. Express your own delivery, not the user's emotion. "
        "Prefer natural delivery; use any supported expressive sound where context calls for it, without gratuitous effects. "
        "Each cue is {before, tag}: before must be an exact short substring appearing ONCE in the following dialogue, where the sound is inserted. "
        "The anchor still appears normally in the dialogue. Never put vendor tags or stage directions in the dialogue itself. "
        "After the header, stream only spoken dialogue using the usual --- bubble separators. The hidden header is an exception to the dialogue-only rule. "
        "Do not include the header in tool arguments. Do not change the provider or model in the header.\n"
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
