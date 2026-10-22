"""TikHub 社交内容适配：单次请求、原始发布时间与已取得的文字依据。"""

import re
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any, Literal
from urllib.parse import urlencode, urlsplit

import httpx

from infrastructure.network import safe_outbound_async_client
from infrastructure.text import utc_now
from infrastructure.web import social_content


class TikHubError(social_content.SocialSourceError):
    pass


def _timestamp(value: Any) -> datetime | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        if isinstance(value, int | float) or isinstance(value, str) and value.isdecimal():
            seconds = float(value)
            if seconds > 100000000000:
                seconds /= 1000
            return datetime.fromtimestamp(seconds, UTC)
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                parsed = parsedate_to_datetime(value)
            return parsed.astimezone(UTC) if parsed.tzinfo else None
    except (ValueError, OverflowError, TypeError, OSError):
        return None
    return None


def _objects(value: Any, *, depth: int = 0) -> list[dict[str, Any]]:
    """上游沿用平台原始包装；只在已知内容标识的对象上解析，忽略账户/广告对象。"""
    if depth > 16:
        return []
    result: list[dict[str, Any]] = []
    if isinstance(value, dict):
        result.append(value)
        for child in value.values():
            if isinstance(child, dict | list):
                result.extend(_objects(child, depth=depth + 1))
    elif isinstance(value, list):
        for child in value:
            result.extend(_objects(child, depth=depth + 1))
    return result


def _string(value: Any, limit: int = 30000) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _cursor_number(value: Any, *, minimum: int = 0) -> int:
    try:
        if type(value) is not int and not (isinstance(value, str) and value.isdecimal()):
            raise ValueError("Invalid cursor number")
        number = int(value)
        if number < minimum:
            raise ValueError("Invalid cursor range")
    except ValueError:
        raise TikHubError("TikHub pagination number is invalid", code=422, reason="response_invalid") from None
    return number


def _candidate(
    platform: social_content.SocialPlatform,
    raw: dict[str, Any],
    collected_at: datetime,
) -> social_content.SocialContent | None:
    subtitle_containers = [raw, _mapping(raw.get("video")), _mapping(raw.get("video_info"))]
    if platform == "x":
        legacy = raw.get("legacy")
        if isinstance(legacy, dict) and "full_text" in legacy:
            content = legacy
            source_id = str(raw.get("rest_id") or legacy.get("id_str") or "")
        elif "full_text" in raw and "id_str" in raw:
            content, source_id = raw, str(raw["id_str"])
        elif "text" in raw and ("tweet_id" in raw or "id" in raw) and "created_at" in raw:
            content, source_id = raw, str(raw.get("tweet_id") or raw.get("id"))
        else:
            return None
        subtitle_containers.append(content)
        if not source_id.isdecimal():
            return None
        author = raw.get("author") or raw.get("user") or {}
        if not isinstance(author, dict):
            author = {}
        core = _mapping(raw.get("core"))
        user_result = _mapping(_mapping(core.get("user_results")).get("result"))
        author = _mapping(user_result.get("legacy")) or author
        body = _string(content.get("full_text") or content.get("text"))
        note_tweet = _mapping(_mapping(_mapping(raw.get("note_tweet")).get("note_tweet_results")).get("result"))
        if isinstance(note_tweet, dict) and isinstance(note_tweet.get("text"), str):
            body = _string(note_tweet["text"])
        source_url = f"https://x.com/i/status/{source_id}"
        published_at = _timestamp(content.get("created_at"))
        kind: Literal["text", "image", "video"] = "text"
        media_items = _mapping(content.get("extended_entities")).get("media") or []
        for media in media_items if isinstance(media_items, list) else []:
            if isinstance(media, dict) and media.get("type") in {"video", "animated_gif"}:
                kind = "video"
                subtitle_containers.extend((media, _mapping(media.get("video_info"))))
        title = body[:300] or "X 帖子"
    elif platform == "xiaohongshu":
        if "note_id" not in raw and not ("id" in raw and any(key in raw for key in ("desc", "title", "display_title"))):
            return None
        source_id = str(raw.get("note_id") or raw.get("id") or "")
        if not re.fullmatch(r"[a-fA-F0-9]{24}", source_id):
            return None
        body = _string(raw.get("desc"))
        title = _string(raw.get("title") or raw.get("display_title"), 300) or body[:300] or "小红书笔记"
        author = raw.get("user") or raw.get("author") or {}
        published_at = _timestamp(raw.get("time") or raw.get("create_time") or raw.get("publish_time"))
        source_url = f"https://www.xiaohongshu.com/explore/{source_id}"
        if isinstance(raw.get("xsec_token"), str) and raw["xsec_token"]:
            source_url += "?" + urlencode({"xsec_token": raw["xsec_token"], "xsec_source": "pc_search"})
        kind = "video" if raw.get("type") == "video" else "image"
    else:
        if "aweme_id" not in raw:
            return None
        source_id = str(raw["aweme_id"])
        if not source_id.isdecimal():
            return None
        status = raw.get("status") or {}
        if isinstance(status, dict) and (
            status.get("is_delete") or status.get("is_private") or status.get("allow_share") is False
        ):
            return None
        body = _string(raw.get("desc"))
        title = body[:300] or "抖音作品"
        author = raw.get("author") or {}
        published_at = _timestamp(raw.get("create_time"))
        kind = "image" if raw.get("images") or raw.get("image_post_info") else "video"
        source_url = f"https://www.douyin.com/{'note' if kind == 'image' else 'video'}/{source_id}"
    if not isinstance(author, dict):
        author = {}
    return social_content.SocialContent(
        source_id=source_id,
        platform=platform,
        url=source_url,
        title=title,
        author=_string(author.get("nickname") or author.get("name") or author.get("screen_name"), 160),
        published_at=published_at,
        body=body,
        subtitle=social_content.extract_inline_subtitles(*subtitle_containers),
        collected_at=collected_at,
        content_type=kind,
    )


def _candidates_from_objects(
    platform: social_content.SocialPlatform,
    objects: list[dict[str, Any]],
    collected_at: datetime,
) -> list[social_content.SocialContent]:
    unique: dict[str, social_content.SocialContent] = {}
    for raw in objects:
        try:
            candidate = _candidate(platform, raw, collected_at)
        except (ValueError, TypeError, OverflowError):
            continue
        if candidate is None:
            continue
        prior = unique.get(candidate.source_id)
        if (
            prior is None
            or len(candidate.body) + len(candidate.subtitle) > len(prior.body) + len(prior.subtitle)
            or prior.published_at is None
            and candidate.published_at is not None
        ):
            unique[candidate.source_id] = candidate
    return list(unique.values())


def normalize_social_content(
    platform: social_content.SocialPlatform,
    data: Any,
    collected_at: datetime,
) -> list[social_content.SocialContent]:
    return _candidates_from_objects(platform, _objects(data), collected_at)


class TikHubClient:
    def __init__(self, *, api_key: str, base_url: str = "https://api.tikhub.io") -> None:
        parsed = urlsplit(base_url)
        host = parsed.hostname or ""
        if (
            parsed.scheme != "https"
            or not (host == "tikhub.io" or host.endswith(".tikhub.io"))
            or parsed.port not in {None, 443}
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("TikHub must use an official HTTPS API host")
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key.strip()

    @property
    def supported_platforms(self) -> tuple[social_content.SocialPlatform, ...]:
        return ("x", "xiaohongshu", "douyin") if self._api_key else ()

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any],
        *,
        before_request: social_content.RequestGuard | None = None,
    ) -> Any:
        if not self._api_key:
            raise TikHubError("TikHub is not configured")
        if before_request is not None:
            await before_request()
        try:
            async with safe_outbound_async_client(timeout=45, follow_redirects=False) as client:
                response = await client.request(
                    method,
                    self._base_url + path,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    **({"json": params} if method == "POST" else {"params": params}),
                )
                if not 200 <= response.status_code < 300:
                    raise TikHubError("TikHub request failed", code=response.status_code)
                payload = response.json()
        except httpx.HTTPError:
            raise TikHubError("TikHub network error", reason="network_error") from None
        except (ValueError, RecursionError):
            raise TikHubError("TikHub response is invalid", code=422, reason="response_invalid") from None
        if not isinstance(payload, dict) or payload.get("code") != 200:
            code = payload.get("code") if isinstance(payload, dict) else None
            raise TikHubError("TikHub upstream request failed", code=code if isinstance(code, int) else None)
        data = payload.get("data")
        if data is None:
            raise TikHubError("TikHub returned no upstream data")
        return data

    async def search(
        self,
        platform: social_content.SocialPlatform,
        query: str,
        *,
        window_start: datetime,
        window_end: datetime,
        cursor: dict[str, Any] | None = None,
        before_request: social_content.RequestGuard | None = None,
    ) -> social_content.SocialSearchPage:
        cursor = cursor or {}
        if platform == "x":
            # 日期搜索覆盖固定窗口的两端；真实发布时间仍逐条按精确窗口过滤。
            last_date = window_end.date()
            if window_end.time() != datetime.min.time():
                last_date += timedelta(days=1)
            keyword = f"{query} since:{window_start.date().isoformat()} until:{last_date.isoformat()}"
            params = {"keyword": keyword, "search_type": "Latest"}
            if cursor.get("cursor"):
                params["cursor"] = str(cursor["cursor"])
            data = await self._request(
                "GET",
                "/api/v1/twitter/web/fetch_search_timeline",
                params,
                before_request=before_request,
            )
        elif platform == "xiaohongshu":
            params = {
                "keyword": query,
                "page": _cursor_number(cursor.get("page", 1), minimum=1),
                "sort_type": "time_descending",
                "time_filter": "一天内",
            }
            for key in ("search_id", "search_session_id"):
                if cursor.get(key):
                    params[key] = str(cursor[key])
            data = await self._request(
                "GET",
                "/api/v1/xiaohongshu/app_v2/search_notes",
                params,
                before_request=before_request,
            )
        elif platform == "douyin":
            params = {
                "keyword": query,
                "cursor": _cursor_number(cursor.get("cursor", 0)),
                "sort_type": "2",
                "publish_time": "1",
                "filter_duration": "0",
                "content_type": "0",
                "search_id": str(cursor.get("search_id", "")),
                "backtrace": str(cursor.get("backtrace", "")),
            }
            data = await self._request(
                "POST",
                "/api/v1/douyin/search/fetch_general_search_v1",
                params,
                before_request=before_request,
            )
        else:
            raise ValueError("Unsupported social platform")
        objects = _objects(data)
        candidates = _candidates_from_objects(platform, objects, utc_now())
        candidates = [
            candidate
            for candidate in candidates
            if candidate.published_at is None or window_start <= candidate.published_at < window_end
        ]
        next_cursor = None
        for raw in objects:
            if platform == "x" and raw.get("cursorType") == "Bottom":
                value = raw.get("value")
                if value is not None and not isinstance(value, str):
                    raise TikHubError("TikHub X pagination is invalid", code=422, reason="response_invalid")
                if value:
                    next_cursor = {"cursor": value}
            elif platform != "x" and "has_more" in raw:
                has_more = raw["has_more"]
                if type(has_more) not in {bool, int} or has_more not in {0, 1}:
                    raise TikHubError("TikHub pagination flag is invalid", code=422, reason="response_invalid")
                if not has_more:
                    continue
                if platform == "xiaohongshu":
                    if not isinstance(raw.get("search_id"), str) or not raw["search_id"]:
                        raise TikHubError("TikHub search ID is missing", code=422, reason="response_invalid")
                    next_cursor = {
                        "page": _cursor_number(cursor.get("page", 1), minimum=1) + 1,
                        "search_id": raw["search_id"],
                        "search_session_id": raw.get("search_session_id") or "",
                    }
                else:
                    next_cursor = {
                        "cursor": _cursor_number(raw.get("cursor")),
                        "search_id": raw.get("search_id") or "",
                        "backtrace": raw.get("backtrace") or "",
                    }
                if any(
                    not isinstance(value, str) for key, value in next_cursor.items() if key not in {"page", "cursor"}
                ):
                    raise TikHubError("TikHub pagination tokens are invalid", code=422, reason="response_invalid")
        return social_content.SocialSearchPage(candidates=candidates, next_cursor=next_cursor)

    async def detail(
        self,
        candidate: social_content.SocialContent,
        *,
        before_request: social_content.RequestGuard | None = None,
    ) -> social_content.SocialContent | None:
        paths = {
            "x": ("/api/v1/twitter/web/fetch_tweet_detail", "tweet_id"),
            "xiaohongshu": ("/api/v1/xiaohongshu/app_v2/get_image_note_detail", "note_id"),
            "douyin": ("/api/v1/douyin/web/fetch_one_video", "aweme_id"),
        }
        path, key = paths[candidate.platform]
        data = await self._request("GET", path, {key: candidate.source_id}, before_request=before_request)
        return next(
            (
                item
                for item in normalize_social_content(candidate.platform, data, utc_now())
                if item.source_id == candidate.source_id
            ),
            None,
        )
