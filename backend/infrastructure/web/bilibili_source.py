"""使用账户 Cookie 的 B 站视频读取适配器；证据只取标题与简介，不读字幕文件。"""

import re
from datetime import datetime
from html import unescape
from typing import Any

from infrastructure.text import utc_now

from .bilibili import BilibiliWebClient
from .social_content import (
    RequestGuard,
    SocialContent,
    SocialSearchPage,
    SocialSourceError,
    validate_source_url,
)
from .social_parsing import mapping as _mapping
from .social_parsing import text as _text
from .social_parsing import timestamp as _timestamp

_BVID = re.compile(r"BV[0-9A-Za-z]{10}")
_PAGE_SIZE = 20
_MAX_PAGE = 100


def _plain(value: Any) -> str:
    """搜索结果标题带关键词高亮标签，先去标签再取文字。"""
    return unescape(re.sub(r"<[^>]+>", "", _text(value, 600)))


def _video_source(
    source_id: str, *, title: Any, author: Any, published_at: Any, body: Any, collected_at: datetime
) -> SocialContent | None:
    body_text = _text(body)
    try:
        return SocialContent(
            source_id=source_id,
            platform="bilibili",
            url=validate_source_url("bilibili", source_id, f"https://www.bilibili.com/video/{source_id}"),
            title=_plain(title)[:300] or body_text[:300] or "B 站视频",
            author=_text(author, 160),
            published_at=_timestamp(published_at),
            body=body_text,
            content_type="video",
            collected_at=collected_at,
        )
    except (ValueError, TypeError):
        return None


class BilibiliSource:
    def __init__(self, *, cookies: dict[str, str]) -> None:
        self._bilibili = BilibiliWebClient(cookies=cookies)

    async def search(
        self,
        query: str,
        *,
        window_start: datetime,
        window_end: datetime,
        cursor: dict[str, Any] | None = None,
        before_request: RequestGuard | None = None,
    ) -> SocialSearchPage:
        page = 1
        if cursor is not None:
            if type(cursor.get("page")) is not int or not 1 <= cursor["page"] <= _MAX_PAGE:
                raise SocialSourceError("Bilibili search cursor is invalid", code=422, reason="response_invalid")
            page = cursor["page"]
        data = await self._bilibili.search(
            query, window_start=window_start, window_end=window_end, page=page, before_request=before_request
        )
        if data is None:
            results: list[Any] = []
        elif isinstance(data, dict) and isinstance(data.get("result"), list):
            results = data["result"]
        else:
            raise SocialSourceError("Bilibili search shape changed", code=422, reason="response_invalid")
        candidates: list[SocialContent] = []
        for item in results:
            if not isinstance(item, dict) or item.get("type") != "video":
                continue
            source_id = _text(item.get("bvid"), 32)
            if not _BVID.fullmatch(source_id):
                continue
            source = _video_source(
                source_id,
                title=item.get("title"),
                author=item.get("author") or item.get("uname"),
                published_at=item.get("pubdate"),
                body=item.get("description"),
                collected_at=utc_now(),
            )
            if source is not None:
                candidates.append(source)
        inside = [
            source
            for source in candidates
            if source.published_at is not None and window_start <= source.published_at < window_end
        ]
        # 平台搜索页数有上限，触顶后不再给出游标，避免检查点保存无法请求的页码。
        more = len(results) >= _PAGE_SIZE and page < _MAX_PAGE
        return SocialSearchPage(candidates=inside, next_cursor={"page": page + 1} if more else None)

    async def detail(
        self, candidate: SocialContent, *, before_request: RequestGuard | None = None
    ) -> SocialContent | None:
        if candidate.platform != "bilibili":
            raise SocialSourceError("Unexpected source platform", code=400, reason="unsupported_platform")
        if not _BVID.fullmatch(candidate.source_id):
            raise SocialSourceError("Invalid Bilibili content ID", code=422, reason="response_invalid")
        data = await self._bilibili.video_detail(candidate.source_id, before_request=before_request)
        if data is None:
            return None
        if not isinstance(data, dict) or data.get("bvid") != candidate.source_id:
            raise SocialSourceError("Bilibili detail does not match the source", code=422, reason="response_invalid")
        return _video_source(
            candidate.source_id,
            title=data.get("title"),
            author=_mapping(data.get("owner")).get("name"),
            published_at=data.get("pubdate"),
            body=data.get("desc"),
            collected_at=utc_now(),
        )
