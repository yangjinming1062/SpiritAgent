import json
import re
from copy import deepcopy
from typing import Annotated, Literal

from components import resolve_prompt_text
from modules.conversation import (
    CompanionReply,
    CompanionReplyInput,
    MediaBubble,
    MediaBubbleInput,
    TextBubble,
    VoiceBubble,
)
from prompts.chat import COMPANION_DIALOGUE_FIELD_GUIDANCES
from pydantic import BaseModel, ConfigDict, Field, RootModel, ValidationError

from services.contracts import MediaTurnState
from services.domains.conversation import resolve_reply_media
from services.infrastructure.llm import ProviderConfig, speech_performance_schema, validate_speech_style


class _ReplyEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _PreserveReply(_ReplyEdit):
    action: Literal["preserve"]


class _DialogueEdit(_ReplyEdit):
    action: Literal["dialogue"]
    bubbles: CompanionReplyInput


class _WrittenEdit(_ReplyEdit):
    action: Literal["written"]
    text: str


_ReplyEditDecision = RootModel[Annotated[_PreserveReply | _DialogueEdit | _WrittenEdit, Field(discriminator="action")]]


def _dialogue_text_pattern(language: str) -> str:
    endings = "。！？!?" + ("." if language == "en" else "")
    return rf"^[^\r\n\\（）()*{endings}]*[{endings}]?$"


def normalize_companion_reply_content(raw: str) -> str:
    """归一完整 JSON 的外层表示；内容与字段仍由气泡模型严格校验。"""
    fenced = re.fullmatch(r"```(?:json)?[ \t]*\r?\n([\s\S]*?)\r?\n```", raw.strip(), flags=re.IGNORECASE)
    content = fenced[1] if fenced else raw
    try:
        value = json.loads(content)
    except ValueError:
        return content
    if isinstance(value, dict) and value.get("type") in ("text", "voice", "image", "video"):
        return f"[{content}]"
    return content


def _invalid_performance(index: int, path: tuple[str | int, ...], message: str) -> ValidationError:
    return ValidationError.from_exception_data(
        "CompanionReplyInput",
        [{"type": "value_error", "loc": (index, "voice", "speech", *path), "ctx": {"error": ValueError(message)}}],
    )


def companion_reply_schema(
    speech_config: ProviderConfig | None,
    *,
    language: str,
    allow_silence: bool,
    allow_media: bool,
) -> dict:
    schema = CompanionReplyInput.model_json_schema()
    schema["minItems"] = 0 if allow_silence else 1
    definitions = schema["$defs"]
    bubble_types = ["TextBubble"]
    if allow_media:
        bubble_types.append("MediaBubbleInput")
    if speech_config is not None:
        bubble_types.append("VoiceBubbleInput")
    schema["items"] = {"oneOf": [{"$ref": f"#/$defs/{name}"} for name in bubble_types]}
    schema["$defs"] = {name: definitions[name] for name in bubble_types}
    if speech_config is not None:
        performance = speech_performance_schema(speech_config.provider_name, speech_config.model)
        schema["$defs"].update({**performance.pop("$defs", {}), "SpeechPerformance": performance})
    for name in ("TextBubble", "VoiceBubbleInput"):
        if name in schema["$defs"]:
            schema["$defs"][name]["properties"]["text"]["description"] = resolve_prompt_text(
                COMPANION_DIALOGUE_FIELD_GUIDANCES,
                language,
            )
    return schema


def _can_preserve_written_reply(draft: str) -> bool:
    values = json.loads(draft)
    if len(values) != 1:
        return False
    try:
        TextBubble.model_validate(values[0])
    except ValueError:
        return False
    return True


def companion_reply_edit_schema(schema: dict, draft: str, *, language: str) -> dict:
    """编辑决定单独校验，只有日常台词分支使用句子边界约束。"""
    edit_schema = _ReplyEditDecision.model_json_schema()
    definitions = edit_schema["$defs"]
    if not _can_preserve_written_reply(draft):
        edit_schema["oneOf"] = [item for item in edit_schema["oneOf"] if item["$ref"] != "#/$defs/_PreserveReply"]
        del edit_schema["discriminator"]["mapping"]["preserve"]
        del definitions["_PreserveReply"]
    definitions.update(deepcopy(schema["$defs"]))
    definitions["CompanionReplyInput"] = {key: value for key, value in schema.items() if key != "$defs"}
    definitions["_WrittenEdit"]["properties"]["text"] = {
        key: value for key, value in definitions["TextBubble"]["properties"]["text"].items() if key != "description"
    }
    for name in ("TextBubble", "VoiceBubbleInput"):
        if name in definitions:
            definitions[name]["properties"]["text"] = {
                **definitions[name]["properties"]["text"],
                "pattern": _dialogue_text_pattern(language),
            }
    return edit_schema


def can_edit_companion_reply(raw: str) -> bool:
    try:
        values = json.loads(raw)
    except ValueError:
        return False
    return (
        isinstance(values, list)
        and bool(values)
        and all(
            isinstance(value, dict) and value.get("type") in {"text", "voice"} and isinstance(value.get("text"), str)
            for value in values
        )
    )


def apply_companion_reply_edit(raw: str, draft: str, *, language: str) -> str:
    decision = _ReplyEditDecision.model_validate_json(raw).root
    if isinstance(decision, _PreserveReply):
        if not _can_preserve_written_reply(draft):
            raise ValueError("Preserving a written reply requires one valid text bubble")
        return draft
    original = json.loads(draft)
    if isinstance(decision, _WrittenEdit):
        if any(bubble["type"] != "text" for bubble in original):
            raise ValueError("A written edit cannot remove voice or media bubbles")
        if re.sub(r"\s+", "", decision.text) != re.sub(r"\s+", "", "".join(b["text"] for b in original)):
            raise ValueError("A written edit must preserve the draft's words and punctuation")
        values = [TextBubble(type="text", text=decision.text).model_dump()]
    else:
        values = json.loads(raw)["bubbles"]
        if any(
            not re.fullmatch(_dialogue_text_pattern(language), bubble["text"])
            for bubble in values
            if bubble["type"] in {"text", "voice"}
        ):
            raise ValueError("Each edited dialogue bubble requires one sentence without line breaks")
    if [(b["type"], b["media_id"]) for b in values if "media_id" in b] != [
        (b["type"], b["media_id"]) for b in original if "media_id" in b
    ]:
        raise ValueError("A reply edit must preserve media references and order")
    return json.dumps(values, ensure_ascii=False)


def parse_companion_reply(
    raw: str,
    *,
    speech_config: ProviderConfig | None,
    voice_id: str,
    language: str,
    allow_silence: bool,
    media_turn: MediaTurnState,
) -> CompanionReply | None:
    source = CompanionReplyInput.model_validate_json(raw)
    if not source.root:
        if allow_silence and not media_turn.required_goals:
            return None
        raise ValueError("A user reply requires at least one bubble")
    bubbles: list[TextBubble | VoiceBubble | MediaBubble] = []
    selected_goals: set[str] = set()
    for index, bubble in enumerate(source.root):
        if isinstance(bubble, MediaBubbleInput):
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
            raise _invalid_performance(index, (), "Voice is unavailable; deliver this dialogue as text")
        if not bubble.speech.model_fields_set:
            raise _invalid_performance(index, (), "Voice reply requires per-bubble performance")
        try:
            style = bubble.speech.bind(speech_config.provider_name, speech_config.model)
            validate_speech_style(style, speech_config.provider_name, speech_config.model)
        except ValidationError as exc:
            raise ValidationError.from_exception_data(
                "CompanionReplyInput",
                [
                    {**error, "loc": (index, "voice", "speech", *error["loc"][1:])}
                    for error in exc.errors(include_url=False)
                ],
            ) from exc
        for cue_index, cue in enumerate(style.cues):
            if bubble.text.count(cue.before) != 1:
                raise _invalid_performance(
                    index,
                    ("cues", cue_index, "before"),
                    "Cue anchor must be an exact phrase occurring once in this bubble's text; choose a unique phrase or omit the cue",
                )
        if style.provider == "minimax":
            positions: set[int] = set()
            for pause_index, pause in enumerate(style.pauses):
                if bubble.text.count(pause.before) != 1:
                    raise _invalid_performance(
                        index,
                        ("pauses", pause_index, "before"),
                        "Pause anchor must be an exact phrase occurring once in this bubble's text",
                    )
                position = bubble.text.index(pause.before)
                if not bubble.text[:position].strip() or position in positions:
                    raise _invalid_performance(
                        index,
                        ("pauses", pause_index, "before"),
                        "Pause must follow spoken words and use a different anchor from other pauses",
                    )
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
    if not media_turn.required_goals.issubset(selected_goals):
        raise ValueError("Reply omits generated media or an accepted video task")
    return CompanionReply(bubbles=bubbles)


def fallback_companion_voice_reply(
    raw: str,
    *,
    speech_config: ProviderConfig | None,
    voice_id: str,
    language: str,
    allow_silence: bool,
    media_turn: MediaTurnState,
) -> tuple[str, CompanionReply | None]:
    """恢复预算耗尽后仅将演绎无效的气泡降级为原台词；正文和媒体仍须通过完整校验。"""
    values = json.loads(raw)
    while True:
        try:
            return raw, parse_companion_reply(
                raw,
                speech_config=speech_config,
                voice_id=voice_id,
                language=language,
                allow_silence=allow_silence,
                media_turn=media_turn,
            )
        except ValidationError as exc:
            paths = [error["loc"] for error in exc.errors()]
            if not isinstance(values, list) or not all(
                len(path) >= 3 and isinstance(path[0], int) and path[1:3] == ("voice", "speech") for path in paths
            ):
                raise
            for index in {path[0] for path in paths}:
                bubble = values[index]
                if not isinstance(bubble, dict) or bubble.get("type") != "voice":
                    raise
                values[index] = {"type": "text", "text": bubble.get("text")}
            raw = json.dumps(values, ensure_ascii=False)
