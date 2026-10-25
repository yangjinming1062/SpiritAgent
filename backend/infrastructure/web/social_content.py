"""社交内容的供应商无关契约与原文地址校验。"""

import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .social_platforms import SocialPlatform, get_social_platform

RequestGuard = Callable[[], Awaitable[None]]


class SocialSourceError(RuntimeError):
    def __init__(self, message: str, *, code: int | None = None, reason: str = "source_failed") -> None:
        super().__init__(message)
        self.code = code
        self.reason = reason


def failure_reason(status_code: int) -> str:
    return "login_required" if status_code in {401, 403} else "rate_limited" if status_code == 429 else "source_failed"


def validate_platform_url(platform: SocialPlatform, url: str) -> str:
    """平台链接只能使用注册的 HTTPS 原文域名。"""
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in {None, 443}:
        raise ValueError("Invalid topic source URL")
    definition = get_social_platform(platform)
    if parsed.hostname not in definition.hosts or parsed.fragment:
        raise ValueError("Invalid topic source host")
    return url


def validate_source_url(platform: SocialPlatform, source_id: str, url: str) -> str:
    """只接受能与平台内容 ID 对应的 HTTPS 原文地址，不接受短链和跳转链接。"""
    parsed = urlsplit(validate_platform_url(platform, url))
    definition = get_social_platform(platform)
    if not re.fullmatch(definition.path_pattern.format(source_id=re.escape(source_id)), parsed.path):
        raise ValueError("Topic source URL does not match content ID")
    query = parse_qs(parsed.query)
    return urlunsplit(
        (
            "https",
            parsed.hostname,
            parsed.path,
            urlencode({key: values[0] for key, values in query.items() if key in definition.query_keys}),
            "",
        ),
    )


class SocialContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1, max_length=96)
    platform: SocialPlatform
    url: str = Field(max_length=2048)
    title: str = Field(min_length=1, max_length=300)
    author: str = Field(default="", max_length=160)
    published_at: datetime | None = None
    body: str = Field(default="", max_length=30000)
    subtitle: str = Field(default="", max_length=30000)
    collected_at: datetime
    content_type: Literal["text", "image", "video"] = "text"

    @field_validator("source_id")
    @classmethod
    def check_source_id(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
            raise ValueError("Invalid social content ID")
        return value

    @field_validator("published_at", "collected_at")
    @classmethod
    def check_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("Social timestamps must include timezone")
        return value.astimezone(UTC) if value is not None else None

    @property
    def has_evidence(self) -> bool:
        return bool(self.body.strip() or self.subtitle.strip())


class SocialSearchPage(BaseModel):
    candidates: list[SocialContent]
    next_cursor: dict[str, Any] | None = None


def _inline_subtitle(value: Any, *, depth: int = 0) -> str:
    """只提取响应内已经存在的字幕文字；播放地址和字幕文件地址都不是文字依据。"""
    if depth > 8:
        return ""
    if isinstance(value, str):
        text = value.strip()[:30000]
        return "" if re.fullmatch(r"(?:https?://|//)\S+", text, re.IGNORECASE) else text
    if isinstance(value, list):
        parts = [_inline_subtitle(item, depth=depth + 1) for item in value]
        return "\n".join(part for part in parts if part)[:30000]
    if isinstance(value, dict):
        for key in ("text", "content", "subtitle_text", "caption_text"):
            text = _inline_subtitle(value.get(key), depth=depth + 1)
            if text:
                return text
        for key in ("segments", "subtitles", "captions", "items"):
            text = _inline_subtitle(value.get(key), depth=depth + 1)
            if text:
                return text
    return ""


def extract_inline_subtitles(*containers: dict[str, Any]) -> str:
    candidates: list[str] = []
    for container in containers:
        for key in (
            "subtitle",
            "subtitles",
            "subtitle_info",
            "subtitle_infos",
            "subtitle_text",
            "caption",
            "captions",
            "caption_infos",
            "caption_text",
        ):
            text = _inline_subtitle(container.get(key))
            if text:
                candidates.append(text)
    return max(candidates, key=len, default="")
