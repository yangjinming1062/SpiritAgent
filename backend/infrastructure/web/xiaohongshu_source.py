"""使用账户 Cookie 的小红书读取适配器。"""

import re
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

from pydantic import JsonValue

from infrastructure.text import utc_now

from .social_content import (
    RequestGuard,
    SocialContent,
    SocialSearchPage,
    SocialSourceError,
    extract_inline_subtitles,
    validate_source_url,
)
from .social_parsing import mapping as _mapping
from .social_parsing import text as _text
from .social_parsing import timestamp as _timestamp
from .xiaohongshu import XiaohongshuServiceClient


def _xhs_source(
    note: dict[str, Any],
    *,
    source_id: str,
    token: str,
    collected_at: datetime,
    detail: bool,
) -> SocialContent | None:
    if not re.fullmatch(r"[a-fA-F0-9]{24}", source_id) or not token:
        return None
    user = _mapping(note.get("user"))
    body = _text(note.get("desc")) if detail else ""
    url = f"https://www.xiaohongshu.com/explore/{source_id}?" + urlencode(
        {"xsec_token": token, "xsec_source": "pc_search"},
    )
    try:
        return SocialContent(
            source_id=source_id,
            platform="xiaohongshu",
            url=validate_source_url("xiaohongshu", source_id, url),
            title=_text(note.get("title") or note.get("displayTitle"), 300) or body[:300] or "小红书笔记",
            author=_text(user.get("nickname") or user.get("nickName"), 160),
            published_at=_timestamp(note.get("time")) if detail else None,
            body=body,
            subtitle=extract_inline_subtitles(note, _mapping(note.get("video"))) if detail else "",
            content_type="video" if note.get("type") == "video" else "image",
            collected_at=collected_at,
        )
    except (ValueError, TypeError):
        return None


class XiaohongshuSource:
    def __init__(self, *, client: XiaohongshuServiceClient, cookies: list[dict[str, JsonValue]]) -> None:
        self._xhs = client
        self._cookies = cookies

    async def search(
        self,
        query: str,
        *,
        window_start: datetime,
        window_end: datetime,
        cursor: dict[str, Any] | None = None,
        before_request: RequestGuard | None = None,
    ) -> SocialSearchPage:
        if cursor:
            raise SocialSourceError(
                "Xiaohongshu source does not support paginated search",
                code=400,
                reason="unsupported_platform",
            )
        data = await self._xhs.search_feeds(query, cookies=self._cookies, before_request=before_request)
        feeds = data.get("feeds")
        if not isinstance(feeds, list):
            raise SocialSourceError("Xiaohongshu search shape changed", code=422, reason="response_invalid")
        candidates: list[SocialContent] = []
        for feed in feeds:
            if not isinstance(feed, dict) or feed.get("modelType") != "note":
                continue
            source = _xhs_source(
                _mapping(feed.get("noteCard")),
                source_id=_text(feed.get("id"), 96),
                token=_text(feed.get("xsecToken"), 1600),
                collected_at=utc_now(),
                detail=False,
            )
            if source is not None:
                candidates.append(source)
        return SocialSearchPage(candidates=list({candidate.source_id: candidate for candidate in candidates}.values()))

    async def detail(
        self,
        candidate: SocialContent,
        *,
        before_request: RequestGuard | None = None,
    ) -> SocialContent | None:
        if candidate.platform != "xiaohongshu":
            raise SocialSourceError("Unexpected source platform", code=400, reason="unsupported_platform")
        try:
            token = parse_qs(
                urlsplit(validate_source_url(candidate.platform, candidate.source_id, candidate.url)).query,
            ).get("xsec_token", [""])[0]
        except ValueError:
            raise SocialSourceError("Xiaohongshu source URL is invalid", code=422, reason="response_invalid") from None
        if not token:
            raise SocialSourceError("Xiaohongshu source token is missing", code=422, reason="response_invalid")
        data = await self._xhs.feed_detail(
            candidate.source_id, token, cookies=self._cookies, before_request=before_request
        )
        note = _mapping(_mapping(data.get("data")).get("note"))
        if note.get("noteId") != candidate.source_id:
            raise SocialSourceError("Xiaohongshu detail does not match the source", code=422, reason="response_invalid")
        return _xhs_source(note, source_id=candidate.source_id, token=token, collected_at=utc_now(), detail=True)
