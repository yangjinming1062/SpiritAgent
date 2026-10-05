import json
import re
from collections import Counter
from copy import deepcopy
from typing import Literal

from components import resolve_prompt_text
from modules.conversation import (
    CompanionReply,
    CompanionReplyInput,
    MediaBubble,
    MediaBubbleInput,
    TextBubble,
    VoiceBubble,
)
from prompts.chat import COMPANION_DIALOGUE_FIELD_GUIDANCES, COMPANION_WRITTEN_FIELD_GUIDANCES
from pydantic import BaseModel, ConfigDict, JsonValue, ValidationError

from services.contracts import MediaTurnState
from services.domains.conversation import resolve_reply_media
from services.infrastructure.llm import ProviderConfig, speech_performance_schema, validate_speech_style


class _ReplyEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["dialogue", "written"]
    bubbles: list[dict[str, JsonValue]]


# 英文句点还可用于小数、缩写和省略号，交付前单独检查句界。
_ENDING_CHARACTERS = r"[ \t~～…♡♥❤\"'”’」』☀-➿🇦-🫿\uFE0F\u200D]"
_END_DECORATION = _ENDING_CHARACTERS + "*"
_ENDING_SUFFIX = re.compile(_END_DECORATION)
_DIALOGUE_TEXT_PATTERN = r"^[^\r\n\\（）()*。！？!?]*[。！？!?]*" + _END_DECORATION + "$"
_SENTENCE_END = re.compile(r"[。！？!?]+|(?<!\.)\.(?!\.)(?=" + _ENDING_CHARACTERS + r"|\s|$)")
_ABBREVIATION = re.compile(r"\b(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St|vs|etc|e\.g|i\.e|(?:[A-Z]\.)+[A-Z])\.$", re.IGNORECASE)


def normalize_companion_reply_content(raw: str) -> str:
    """只恢复完整响应的 JSON 外层与闭合符，不改字符串内容或抽取夹在正文中的 JSON。"""
    fenced = re.fullmatch(r"```(?:json)?[ \t]*\r?\n([\s\S]*?)\r?\n```", raw.strip(), flags=re.IGNORECASE)
    content = fenced[1] if fenced else raw
    try:
        json.loads(content)
    except ValueError:
        stripped = content.strip()
        if not stripped.startswith("{") or not stripped.endswith("}"):
            return content
        closers: list[str] = []
        output: list[str] = []
        in_string = escaped = False
        for index, char in enumerate(stripped):
            if in_string:
                output.append(char)
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
            elif char == '"':
                in_string = True
                output.append(char)
            elif char in "{[":
                closers.append("}" if char == "{" else "]")
                output.append(char)
            elif char == "," and closers and closers[-1] == "}" and "]" in closers:
                if stripped[index + 1 :].lstrip().startswith("{"):
                    while closers[-1] != "]":
                        output.append(closers.pop())
                output.append(char)
            elif char in "}]":
                if not closers or char not in closers:
                    return content
                while closers[-1] != char:
                    output.append(closers.pop())
                output.append(closers.pop())
            else:
                output.append(char)
        if in_string or closers:
            return content
        repaired = "".join(output)
        try:
            json.loads(repaired)
        except ValueError:
            return content
        return repaired
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
    schema = _ReplyEnvelope.model_json_schema()
    definitions = CompanionReplyInput.model_json_schema()["$defs"]
    bubble_types = ["TextBubble"]
    if allow_media:
        bubble_types.append("MediaBubbleInput")
    if speech_config is not None:
        bubble_types.append("VoiceBubbleInput")
    definitions = schema["$defs"] = {name: definitions[name] for name in bubble_types}
    schema["properties"]["bubbles"] = {
        "type": "array",
        "minItems": 0 if allow_silence else 1,
        "items": {"anyOf": [{"$ref": f"#/$defs/{name}"} for name in bubble_types]},
    }
    if speech_config is not None:
        performance = speech_performance_schema(speech_config.provider_name, speech_config.model)
        schema["$defs"].update({**performance.pop("$defs", {}), "SpeechPerformance": performance})
    if "VoiceBubbleInput" in definitions:
        definitions["VoiceBubbleInput"]["properties"]["text"]["description"] = resolve_prompt_text(
            COMPANION_DIALOGUE_FIELD_GUIDANCES,
            language,
        )
    definitions["TextBubble"]["properties"]["text"]["description"] = (
        "kind=dialogue: "
        + resolve_prompt_text(COMPANION_DIALOGUE_FIELD_GUIDANCES, language)
        + " kind=written: "
        + resolve_prompt_text(COMPANION_WRITTEN_FIELD_GUIDANCES, language)
    )
    return schema


def _draft_bubbles(raw: str) -> list[dict] | None:
    try:
        values = json.loads(normalize_companion_reply_content(raw))
    except ValueError:
        return None
    if isinstance(values, dict):
        values = values.get("bubbles", [values] if "type" in values else None)
    return values if isinstance(values, list) and all(isinstance(value, dict) for value in values) else None


def _sentence_parts(text: str) -> list[str]:
    parts: list[str] = []
    start = 0
    for match in _SENTENCE_END.finditer(text):
        if match[0] == "." and _ABBREVIATION.search(text[: match.end()]):
            continue
        end = _ENDING_SUFFIX.match(text, match.end()).end()
        if part := text[start:end].strip():
            parts.append(part)
        start = end
    if part := text[start:].strip():
        parts.append(part)
    return parts or [text]


def _split_dialogue_bubble(bubble: dict[str, JsonValue], index: int) -> list[dict[str, JsonValue]]:
    if bubble.get("type") not in {"text", "voice"} or not isinstance(bubble.get("text"), str):
        return [bubble]
    parts = _sentence_parts(bubble["text"])
    if len(parts) == 1:
        return [bubble]
    bubbles = [{**deepcopy(bubble), "text": part} for part in parts]
    speech = bubble.get("speech")
    if bubble["type"] == "voice" and isinstance(speech, dict):
        performances = [deepcopy(speech) for _ in parts]
        # 锚点随原台词进入所属句子；跨句、无效或落在新气泡开头的停顿交给模型修正。
        for field in ("cues", "pauses"):
            anchors = speech.get(field)
            if not isinstance(anchors, list):
                continue
            selected: list[list[JsonValue]] = [[] for _ in parts]
            for anchor_index, anchor in enumerate(anchors):
                if not isinstance(anchor, dict) or not isinstance(anchor.get("before"), str):
                    raise _invalid_performance(index, (field, anchor_index, "before"), "Use a phrase from this bubble")
                matching = [i for i, part in enumerate(parts) if part.count(anchor["before"]) == 1]
                if len(matching) != 1 or (field == "pauses" and parts[matching[0]].startswith(anchor["before"])):
                    raise _invalid_performance(
                        index,
                        (field, anchor_index, "before"),
                        "Anchor must match a unique phrase in one split sentence; adjust or omit this optional control",
                    )
                selected[matching[0]].append(anchor)
            for performance, anchors in zip(performances, selected, strict=True):
                performance[field] = anchors
        for value, performance in zip(bubbles, performances, strict=True):
            value["speech"] = performance
    return bubbles


def _fallback_speech_bubbles(values: object, error: ValidationError) -> list[dict]:
    paths = [item["loc"] for item in error.errors()]
    if (
        not isinstance(values, list)
        or not paths
        or not all(len(path) >= 3 and isinstance(path[0], int) and path[1:3] == ("voice", "speech") for path in paths)
    ):
        raise error
    values = list(values)
    for index in {path[0] for path in paths}:
        bubble = values[index]
        if not isinstance(bubble, dict) or bubble.get("type") != "voice":
            raise error
        values[index] = {"type": "text", "text": bubble.get("text")}
    return values


def decode_companion_reply(
    raw: str,
    *,
    allow_voice_fallback: bool = False,
) -> tuple[str, Literal["dialogue", "written"]]:
    """模型类型标记只用于交付校验；持久化继续使用原气泡数组契约。"""
    # 演绎与媒体在数组协议中校验，保留按 speech 字段降级的边界。
    draft = _ReplyEnvelope.model_validate_json(raw)
    values = draft.bubbles
    if draft.kind == "written":
        if not values:
            raise ValueError("A written work requires text content")
        for value in values:
            TextBubble.model_validate(value)
        if len(values) > 1:
            values = [{"type": "text", "text": "\n\n".join(value["text"] for value in values)}]
            TextBubble.model_validate(values[0])
    else:
        values = []
        for index, bubble in enumerate(draft.bubbles):
            try:
                parts = _split_dialogue_bubble(bubble, index)
            except ValidationError as exc:
                if not allow_voice_fallback:
                    raise
                fallback = _fallback_speech_bubbles(draft.bubbles, exc)
                parts = _split_dialogue_bubble(fallback[index], index)
            values.extend(parts)
        errors = []
        for index, bubble in enumerate(values):
            if bubble.get("type") not in {"text", "voice"} or not isinstance(bubble.get("text"), str):
                continue
            text = bubble["text"]
            endings = [
                match
                for match in _SENTENCE_END.finditer(text)
                if match[0] != "." or not _ABBREVIATION.search(text[: match.end()])
            ]
            if not re.fullmatch(_DIALOGUE_TEXT_PATTERN, text) or len(endings) > 1:
                errors.append(
                    {
                        "type": "value_error",
                        "loc": ("bubbles", index, bubble["type"], "text"),
                        "ctx": {
                            "error": ValueError(
                                "Use one spoken sentence per bubble without line breaks or narration markers",
                            ),
                        },
                    },
                )
        if errors:
            raise ValidationError.from_exception_data("CompanionDialogue", errors)
    return json.dumps(values, ensure_ascii=False), draft.kind


def validate_companion_reply_repair(
    content: str,
    kind: Literal["dialogue", "written"],
    draft: str,
    *,
    media_turn: MediaTurnState,
) -> None:
    original = _draft_bubbles(draft)
    if original is None:
        return
    values = json.loads(content)
    if (
        kind == "written"
        and original
        and all(b.get("type") == "text" and isinstance(b.get("text"), str) for b in original)
        and re.sub(r"\s+", "", values[0]["text"]) != re.sub(r"\s+", "", "".join(b["text"] for b in original))
    ):
        raise ValueError("A written repair must preserve the draft's words and punctuation")
    references: dict[tuple[str, str], str] = {}
    for bubble in original:
        if bubble.get("type") not in {"image", "video"} or not isinstance(bubble.get("media_id"), str):
            continue
        try:
            media = resolve_reply_media(media_turn, bubble["media_id"], bubble["type"])
        except ValueError:
            continue
        references[bubble["type"], bubble["media_id"]] = media.goal_id
    goal_counts = Counter(references.values())
    required = [key for key, goal in references.items() if goal_counts[goal] == 1]
    preserved = [(b["type"], b["media_id"]) for b in values if (b.get("type"), b.get("media_id")) in references]
    preserved = [key for key in preserved if key in required]
    if preserved != required:
        raise ValueError("A reply repair must preserve valid media references and order")


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
            values = _fallback_speech_bubbles(values, exc)
            raw = json.dumps(values, ensure_ascii=False)
