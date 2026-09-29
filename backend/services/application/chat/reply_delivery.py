from modules.conversation import (
    CompanionReply,
    CompanionReplyInput,
    MediaBubble,
    MediaBubbleInput,
    TextBubble,
    VoiceBubble,
)
from modules.media import SPEECH_STYLE_ADAPTER

from services.contracts import MediaTurnState
from services.domains.conversation import resolve_reply_media
from services.infrastructure.llm import ProviderConfig, speech_performance_schema, speech_style_matches


def companion_reply_schema(speech_config: ProviderConfig | None, *, allow_silence: bool) -> dict:
    schema = CompanionReplyInput.model_json_schema()
    schema["minItems"] = 0 if allow_silence else 1
    definitions = schema["$defs"]
    if speech_config is None:
        schema["items"] = {"oneOf": [{"$ref": "#/$defs/TextBubble"}, {"$ref": "#/$defs/MediaBubbleInput"}]}
        schema["$defs"] = {name: definitions[name] for name in ("TextBubble", "MediaBubbleInput")}
    else:
        performance = speech_performance_schema(speech_config.provider_name, speech_config.model)
        schema["$defs"] = {
            "TextBubble": definitions["TextBubble"],
            "MediaBubbleInput": definitions["MediaBubbleInput"],
            "VoiceBubbleInput": definitions["VoiceBubbleInput"],
            **performance.pop("$defs", {}),
            "SpeechPerformance": performance,
        }
    return schema


def parse_companion_reply(
    raw: str,
    *,
    speech_config: ProviderConfig | None,
    voice_id: str,
    language: str,
    allow_silence: bool = False,
    media_turn: MediaTurnState | None = None,
) -> CompanionReply | None:
    source = CompanionReplyInput.model_validate_json(raw)
    if not source.root:
        if allow_silence and not (media_turn and media_turn.required_goals):
            return None
        raise ValueError("A user reply requires at least one bubble")
    bubbles: list[TextBubble | VoiceBubble | MediaBubble] = []
    selected_goals: set[str] = set()
    for bubble in source.root:
        if isinstance(bubble, MediaBubbleInput):
            if media_turn is None:
                raise ValueError("Media reference has no conversation context")
            delivered = resolve_reply_media(media_turn, bubble.media_id, bubble.type)
            if delivered.goal_id in selected_goals:
                raise ValueError("Select exactly one media version per goal")
            selected_goals.add(delivered.goal_id)
            bubbles.append(delivered)
            continue
        if bubble.type == "text":
            bubbles.append(bubble)
            continue
        if speech_config is None:
            raise ValueError("Reply bubble type must be 'text'")
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
            raise ValueError("Speech performance does not match the required speech schema")
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
    if media_turn and not media_turn.required_goals.issubset(selected_goals):
        raise ValueError("Reply omits generated media or an accepted video task")
    return CompanionReply(bubbles=bubbles)
