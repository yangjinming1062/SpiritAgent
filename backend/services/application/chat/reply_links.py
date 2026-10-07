"""陪伴台词中的媒体地址只允许引用输入资料；交付产物仍走媒体气泡。"""

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import unquote

from pydantic import JsonValue, ValidationError

_REFERENCE = re.compile(
    r"(?:https?://|file://|blob:|data:(?:image|audio|video)/|www\.|(?<![\w:/])//"
    r"|/?(?:api/(?:companion/asset|media/(?:files|videos))|companion-assets|desktop-attachments|temp-media)/"
    r"|[A-Za-z]:[\\/]|/(?:Users|home|tmp|var)/|\.\.?/)"
    r"[^\s<>\"'\[\]（）【】，。！？；]+",
    re.IGNORECASE,
)
_MEDIA_EXTENSION = re.compile(
    r"\.(?:avif|bmp|gif|heic|jpe?g|png|svg|webp|mp4|m4v|mov|webm|mkv|avi|mp3|m4a|wav|ogg|opus|flac|aac)"
    r"(?:$|[?#&=/;!])",
    re.IGNORECASE,
)
_STORED_MEDIA = re.compile(
    r"(?:^|/)(?:api/(?:companion/asset|media/(?:files|videos))|companion-assets|desktop-attachments|temp-media)/",
    re.IGNORECASE,
)
_INLINE_MEDIA = re.compile(
    r"!\[[^\]\r\n]*\](?:\([^\r\n]*?\)|\[[^\]\r\n]*\])|<(?:img|audio|video|source)\b[^>]*>",
    re.IGNORECASE,
)


def reply_reference_texts(input_items: list[dict], request: str, *, user_input_indices: list[int]) -> tuple[str, ...]:
    """只取用户文字与工具返回，不把助手历史、工具参数或摘要中的地址当作来源。"""
    texts = [request]
    user_indices = set(user_input_indices)
    for index, item in enumerate(input_items):
        if item.get("type") == "function_call_output":
            content = item.get("output")
        elif index in user_indices and item.get("role") == "user":
            content = item.get("content")
        else:
            continue
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            texts.extend(
                part["text"]
                for part in content
                if isinstance(part, dict)
                and part.get("type") in {"input_text", "text"}
                and isinstance(part.get("text"), str)
            )
    return tuple(texts)


def reference_spans(text: str) -> list[tuple[int, int]]:
    """地址内部的问号、句点不能作为台词句界。"""
    return [(match.start(), match.start() + len(match[0].rstrip(".,;:!?)}"))) for match in _REFERENCE.finditer(text)]


def _references(text: str) -> set[str]:
    return {text[start:end] for start, end in reference_spans(text)}


@dataclass(frozen=True)
class ReplyReferences:
    """引用语料及其地址集合；同一回合多次校验共享一次语料扫描。"""

    texts: tuple[str, ...] = ()
    sources: frozenset[str] = frozenset()

    @classmethod
    def from_texts(cls, texts: tuple[str, ...]) -> "ReplyReferences":
        return cls(texts=texts, sources=frozenset(ref for text in texts for ref in _references(text)))


def validate_reply_links(
    bubbles: list[dict[str, JsonValue]],
    *,
    kind: Literal["dialogue", "written"],
    references: ReplyReferences,
) -> None:
    sources = references.sources
    for index, bubble in enumerate(bubbles):
        if bubble.get("type") not in {"text", "voice"} or not isinstance(text := bubble.get("text"), str):
            continue
        error = None
        for reference in _references(text):
            decoded = unquote(reference)
            stored = bool(_STORED_MEDIA.search(decoded))
            if not (
                stored or _MEDIA_EXTENSION.search(decoded) or decoded.lower().startswith(("data:", "blob:", "file:"))
            ):
                continue
            if reference not in sources:
                error = (
                    "Unverified media address in text; use a tool-issued media_id or report that no output is available"
                )
                break
            if stored and kind != "written":
                error = "Deliver generated assets through image/video bubbles, not asset paths in text"
                break
        if any(
            kind != "written" or not any(match[0] in source for source in references.texts)
            for match in _INLINE_MEDIA.finditer(text)
        ):
            error = "Inline media markup cannot deliver an attachment; use media bubbles or preserve an exact source quotation"
        if error:
            raise ValidationError.from_exception_data(
                "CompanionReplyLinks",
                [
                    {
                        "type": "value_error",
                        "loc": ("bubbles", index, bubble["type"], "text"),
                        "ctx": {"error": ValueError(error)},
                    },
                ],
            )
