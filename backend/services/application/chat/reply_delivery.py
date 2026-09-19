from modules.conversation import CompanionReply, CompanionReplyInput, TextBubble, VoiceBubble
from modules.media import SPEECH_STYLE_ADAPTER

from services.infrastructure.llm import ProviderConfig, speech_style_matches


def parse_companion_reply(
    raw: str,
    *,
    speech_config: ProviderConfig | None,
    voice_id: str,
    language: str,
    allow_silence: bool = False,
) -> CompanionReply | None:
    source = CompanionReplyInput.model_validate_json(raw)
    if not source.bubbles:
        if allow_silence:
            return None
        raise ValueError("A user reply requires at least one bubble")
    bubbles: list[TextBubble | VoiceBubble] = []
    for bubble in source.bubbles:
        if bubble.type == "text":
            bubbles.append(bubble)
            continue
        if speech_config is None:
            raise ValueError("Voice reply requires a configured TTS provider")
        if not bubble.speech.model_fields_set:
            raise ValueError("Voice reply requires per-bubble performance")
        style = SPEECH_STYLE_ADAPTER.validate_python(
            {
                **bubble.speech.model_dump(exclude_unset=True),
                "provider": speech_config.provider_name,
                "model": speech_config.model,
            },
        )
        if not speech_style_matches(style, speech_config.provider_name, speech_config.model):
            raise ValueError("Voice performance exceeds configured TTS capabilities")
        if any(bubble.text.count(cue.before) != 1 for cue in style.cues):
            raise ValueError("Voice cue must match this bubble's dialogue exactly once")
        if style.provider == "minimax":
            positions: set[int] = set()
            for pause in style.pauses:
                if bubble.text.count(pause.before) != 1:
                    raise ValueError("Voice pause must match a unique phrase")
                position = bubble.text.index(pause.before)
                if not bubble.text[:position].strip() or position in positions:
                    raise ValueError("Voice pause must be between words and cannot repeat")
                positions.add(position)
        bubbles.append(
            VoiceBubble(
                type="voice",
                text=bubble.text,
                speech=style,
                voice_id=voice_id,
                language=language,
            ),
        )
    return CompanionReply(bubbles=bubbles)
