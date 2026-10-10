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
from modules.media import SpeechSegment
from prompts.chat import (
    COMPANION_DIALOGUE_FIELD_GUIDANCES,
    COMPANION_REPAIR_TEXT_FIELD_GUIDANCES,
    COMPANION_WRITTEN_FIELD_GUIDANCES,
)
from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter, ValidationError

from services.contracts import MediaTurnState
from services.domains.conversation import resolve_reply_media
from services.infrastructure.llm import ProviderConfig, speech_performance_schema, validate_speech_style

from .reply_links import ReplyReferences, reference_spans, validate_reply_links


class OmittedMediaError(ValueError):
    """回复遗漏了本回合已生成的媒体；``missing`` 是按生成顺序排列的 (类型, media_id)。"""

    def __init__(self, missing: list[tuple[str, str]]) -> None:
        super().__init__("Reply omits generated media or an accepted video task")
        self.missing = missing


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
_SPEECH_SEGMENTS_ADAPTER = TypeAdapter(list[SpeechSegment])


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
    repair: bool = False,
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
        # 空修正表示无可恢复内容，交付时仍使用实际回合的沉默权限。
        "minItems": 0 if allow_silence or repair else 1,
        "items": {"anyOf": [{"$ref": f"#/$defs/{name}"} for name in bubble_types]},
    }
    if speech_config is not None:
        performance = speech_performance_schema(speech_config.provider_name, speech_config.model)
        schema["$defs"].update({**performance.pop("$defs", {}), "SpeechPerformance": performance})
    if repair:
        for name in ("TextBubble", "VoiceBubbleInput"):
            if name in definitions:
                definitions[name]["properties"]["text"]["description"] = resolve_prompt_text(
                    COMPANION_REPAIR_TEXT_FIELD_GUIDANCES,
                    language,
                )
        return schema
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
    for match in _sentence_endings(text):
        end = _ENDING_SUFFIX.match(text, match.end()).end()
        if part := text[start:end].strip():
            parts.append(part)
        start = end
    if part := text[start:].strip():
        parts.append(part)
    return parts or [text]


def _sentence_endings(text: str) -> list[re.Match[str]]:
    references = reference_spans(text)
    return [
        match
        for match in _SENTENCE_END.finditer(text)
        if not any(start <= match.start() < end for start, end in references)
        and (match[0] != "." or not _ABBREVIATION.search(text[: match.end()]))
    ]


def _sentence_positions(text: str, parts: list[str]) -> list[tuple[int, int]]:
    """每个分句在原文中的区间；分句是原文的 strip 切片，从上一个终点起定位首次出现。"""
    positions: list[tuple[int, int]] = []
    cursor = 0
    for part in parts:
        start = text.index(part, cursor)
        positions.append((start, start + len(part)))
        cursor = start + len(part)
    return positions


def _split_segments_by_sentence(
    text: str,
    segments: list[JsonValue],
    positions: list[tuple[int, int]],
    index: int,
) -> list[list[dict[str, JsonValue]]]:
    """句内标记按分句重新分组：段起点所在句获得其标记，跨句段在句界切开，段间与句末空白不归入任何句。"""
    try:
        source = _SPEECH_SEGMENTS_ADAPTER.validate_python(segments)
    except ValidationError as exc:
        raise ValidationError.from_exception_data(
            "CompanionReplyInput",
            [
                {**error, "loc": (index, "voice", "speech", "segments", *error["loc"])}
                for error in exc.errors(include_url=False)
            ],
        ) from exc
    if "".join(segment.text for segment in source) != text:
        raise _invalid_performance(
            index,
            ("segments",),
            "Segment texts must concatenate in order to exactly this bubble's text",
        )
    grouped: list[list[dict[str, JsonValue]]] = [[] for _ in positions]
    offset = 0
    sentence_index = 0
    for segment in source:
        start, end = offset, offset + len(segment.text)
        while sentence_index + 1 < len(positions) and positions[sentence_index][1] <= start:
            sentence_index += 1
        if start == end and (segment.tag is not None or segment.pause is not None):
            grouped[sentence_index].append(segment.model_dump(exclude_unset=True))
        cursor = start
        first = True
        while cursor < end:
            sentence_start, sentence_end = positions[sentence_index]
            cut = min(end, sentence_end)
            piece = text[max(cursor, sentence_start) : cut]
            marked = first and (segment.tag is not None or segment.pause is not None)
            if piece or marked:
                child: dict[str, JsonValue] = {"text": piece}
                if marked:
                    if segment.tag is not None:
                        child["tag"] = segment.tag
                    if segment.pause is not None:
                        child["pause"] = segment.pause
                grouped[sentence_index].append(child)
            first = False
            cursor = cut
            if cursor >= sentence_end and sentence_index + 1 < len(positions):
                sentence_index += 1
            elif cursor < end:
                # 游标已达末句终点却未到段末：剩余是被 strip 掉的尾部空白，无句可分配，直接消费到段末。
                break
        offset = end
    return grouped


def _split_dialogue_bubble(bubble: dict[str, JsonValue], index: int) -> list[dict[str, JsonValue]]:
    if bubble.get("type") not in {"text", "voice"} or not isinstance(bubble.get("text"), str):
        return [bubble]
    parts = _sentence_parts(bubble["text"])
    if len(parts) == 1:
        return [bubble]
    speech = bubble.get("speech")
    grouped: list[list[dict[str, JsonValue]]] | None = None
    if bubble["type"] == "voice" and isinstance(speech, dict):
        segments = speech.get("segments") or []
        if not isinstance(segments, list):
            raise _invalid_performance(index, ("segments",), "Voice reply requires a segments array")
        if segments:
            grouped = _split_segments_by_sentence(
                bubble["text"],
                segments,
                _sentence_positions(bubble["text"], parts),
                index,
            )
    bubbles = []
    for position, part in enumerate(parts):
        child = {**deepcopy(bubble), "text": part}
        if grouped is not None:
            performance = {key: value for key, value in speech.items() if key != "segments"}
            performance["segments"] = grouped[position] or [{"text": part}]
            child["speech"] = performance
        bubbles.append(child)
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
    references: ReplyReferences = ReplyReferences(),
) -> tuple[str, Literal["dialogue", "written"]]:
    """模型类型标记只用于交付校验；持久化继续使用原气泡数组契约。"""
    # 演绎与媒体在数组协议中校验，保留按 speech 字段降级的边界。
    draft = _ReplyEnvelope.model_validate_json(raw)
    # 分句前核对完整地址，防止查询串中的问号被拆开后丢失校验依据。
    validate_reply_links(draft.bubbles, kind=draft.kind, references=references)
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
            plain_text = text
            for start, end in reversed(reference_spans(text)):
                plain_text = plain_text[:start] + "URL" + plain_text[end:]
            if not re.fullmatch(_DIALOGUE_TEXT_PATTERN, plain_text) or len(_sentence_endings(text)) > 1:
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
    references: ReplyReferences = ReplyReferences(),
) -> None:
    original = _draft_bubbles(draft)
    if original is None:
        return
    values = json.loads(content)
    preserve_written = True
    try:
        validate_reply_links(original, kind=kind, references=references)
    except ValidationError:
        # 伪造地址不属于需逐字保留的作品内容；媒体顺序保护仍然适用。
        preserve_written = False
    if (
        kind == "written"
        and preserve_written
        and original
        and all(b.get("type") == "text" and isinstance(b.get("text"), str) for b in original)
        and re.sub(r"\s+", "", values[0]["text"]) != re.sub(r"\s+", "", "".join(b["text"] for b in original))
    ):
        raise ValueError("A written repair must preserve the draft's words and punctuation")
    media_goals: dict[tuple[str, str], str] = {}
    for bubble in original:
        if bubble.get("type") not in {"image", "video"} or not isinstance(bubble.get("media_id"), str):
            continue
        try:
            media = resolve_reply_media(media_turn, bubble["media_id"], bubble["type"])
        except ValueError:
            continue
        media_goals[bubble["type"], bubble["media_id"]] = media.goal_id
    goal_counts = Counter(media_goals.values())
    required = [key for key, goal in media_goals.items() if goal_counts[goal] == 1]
    preserved = [(b["type"], b["media_id"]) for b in values if (b.get("type"), b.get("media_id")) in media_goals]
    preserved = [key for key in preserved if key in required]
    if preserved != required:
        raise ValueError("A reply repair must preserve valid media references and order")


def _deliverable_media(media_turn: MediaTurnState, goals: set[str]) -> list[tuple[str, str]]:
    """每个目标当前可交付的版本（图片取最新就绪版本）；无法解析的目标由后续校验照常报告。"""
    latest: dict[str, tuple[str, str]] = {}
    for artifact in media_turn.artifacts.values():
        if artifact.goal_id not in goals:
            continue
        try:
            resolve_reply_media(media_turn, artifact.media_id, artifact.type)
        except ValueError:
            continue
        latest[artifact.goal_id] = (artifact.type, artifact.media_id)
    return list(latest.values())


def parse_companion_reply(
    raw: str,
    *,
    speech_config: ProviderConfig | None,
    voice_id: str,
    language: str,
    allow_silence: bool,
    media_turn: MediaTurnState,
    references: ReplyReferences = ReplyReferences(),
    kind: Literal["dialogue", "written"] = "dialogue",
) -> CompanionReply | None:
    source = CompanionReplyInput.model_validate_json(raw)
    validate_reply_links(
        [bubble.model_dump(include={"type", "text"}) for bubble in source.root],
        kind=kind,
        references=references,
    )
    if not source.root:
        if allow_silence and not media_turn.required_goals:
            return None
        if media_turn.required_goals and (missing := _deliverable_media(media_turn, media_turn.required_goals)):
            raise OmittedMediaError(missing)
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
        if style.segments and "".join(segment.text for segment in style.segments) != bubble.text:
            raise _invalid_performance(
                index,
                ("segments",),
                "Segment texts must concatenate in order to exactly this bubble's text; keep the spoken words verbatim "
                "and omit markers you cannot place",
            )
        if style.provider == "minimax":
            for segment_index, segment in enumerate(style.segments):
                if segment.pause is not None and (
                    not "".join(s.text for s in style.segments[:segment_index]).strip()
                    or (segment.tag is not None and segment.text == "")
                ):
                    raise _invalid_performance(
                        index,
                        ("segments", segment_index, "pause"),
                        "A pause must follow spoken words, never the opening word; omit it when punctuation already "
                        "separates the words",
                    )
        bubbles.append(
            VoiceBubble(
                type="voice",
                text=bubble.text,
                speech=style,
                voice_id=voice_id,
                language=language,
            ),
        )
    if missing_goals := media_turn.required_goals - selected_goals:
        raise OmittedMediaError(_deliverable_media(media_turn, missing_goals))
    return CompanionReply(bubbles=bubbles)


def fallback_companion_voice_reply(
    raw: str,
    *,
    speech_config: ProviderConfig | None,
    voice_id: str,
    language: str,
    allow_silence: bool,
    media_turn: MediaTurnState,
    references: ReplyReferences = ReplyReferences(),
    kind: Literal["dialogue", "written"] = "dialogue",
) -> tuple[str, CompanionReply | None]:
    """恢复预算耗尽后的最后降级：演绎无效的气泡转为原台词，遗漏的已生成媒体按生成顺序补在末尾；其余内容仍须通过完整校验。"""
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
                references=references,
                kind=kind,
            )
        except ValidationError as exc:
            values = _fallback_speech_bubbles(values, exc)
            raw = json.dumps(values, ensure_ascii=False)
        except OmittedMediaError as exc:
            if not exc.missing or not isinstance(values, list):
                raise
            values = [*values, *({"type": media_type, "media_id": media_id} for media_type, media_id in exc.missing)]
            raw = json.dumps(values, ensure_ascii=False)
