"""有界免费采集：运维配置的 X 登录 Cookie 与自托管小红书读取服务。"""

import asyncio
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
from bs4 import BeautifulSoup
from twscrape.account import TOKEN
from twscrape.api import GQL_FEATURES, OP_SearchTimeline, OP_TweetDetail
from twscrape.models import Tweet, parse_tweet, parse_tweets
from twscrape.utils import encode_params, find_obj
from twscrape.xclid import XClIdAccountError, XClIdGen, XClIdParseError, get_tw_page_text, load_keys

from infrastructure.network import safe_outbound_async_client
from infrastructure.text import utc_now
from infrastructure.web.social_credentials import parse_x_search_cookies

from .social_content import (
    RequestGuard,
    SocialContent,
    SocialPlatform,
    SocialSearchPage,
    SocialSourceError,
    extract_inline_subtitles,
    failure_reason,
    validate_source_url,
)
from .xiaohongshu import XiaohongshuServiceClient

_X_GRAPHQL_URL = "https://x.com/i/api/graphql"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class _XBootstrapClient:
    """固定版本 SDK 的网页/脚本读取端口；不使用 SDK 的客户端、账户池或重试。"""

    def __init__(self, client: httpx.AsyncClient, before_request: RequestGuard | None) -> None:
        self._client = client
        self._before_request = before_request
        self._lock = asyncio.Lock()
        self.guard_error: Exception | None = None

    async def _request(self, method: str, url: str, payload: dict[str, Any] | None = None) -> httpx.Response:
        async with self._lock:
            for _ in range(4):
                parsed = urlsplit(url)
                is_page = parsed.hostname == "x.com"
                is_asset = (
                    parsed.hostname == "abs.twimg.com"
                    and parsed.path.endswith(".js")
                    and parsed.path.startswith(("/x-web/", "/responsive-web/client-web/"))
                )
                if (
                    parsed.scheme != "https"
                    or parsed.port not in {None, 443}
                    or parsed.username
                    or parsed.password
                    or not (is_page or is_asset)
                    or method == "POST"
                    and (not is_page or parsed.path != "/x/migrate")
                ):
                    raise SocialSourceError(
                        "X signing bootstrap target is not allowed",
                        code=422,
                        reason="bootstrap_failed",
                    )
                if self.guard_error is not None:
                    raise self.guard_error
                if self._before_request is not None:
                    try:
                        await self._before_request()
                    except Exception as exc:
                        self.guard_error = exc
                        raise
                response = await self._client.request(method, url, **({"json": payload} if payload is not None else {}))
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise SocialSourceError("X bootstrap redirect is invalid", code=422, reason="bootstrap_failed")
                    url = str(response.url.join(location))
                    if response.status_code in {301, 302, 303}:
                        method, payload = "GET", None
                    continue
                if not 200 <= response.status_code < 300:
                    raise SocialSourceError(
                        "X signing bootstrap request failed",
                        code=response.status_code,
                        reason=failure_reason(response.status_code),
                    )
                return response
        raise SocialSourceError("X bootstrap exceeded redirect limit", code=422, reason="bootstrap_failed")

    async def get(self, url: str) -> httpx.Response:
        return await self._request("GET", url)

    async def post(self, url: str, *, json: dict[str, Any]) -> httpx.Response:
        return await self._request("POST", url, json)


def _text(value: Any, limit: int = 30000) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _timestamp(value: Any) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value / 1000 if value > 100000000000 else value, UTC)
    except (ValueError, OverflowError, OSError):
        return None


def _tweet_source(tweet: Tweet, collected_at: datetime) -> SocialContent | None:
    if tweet.user.protected:
        return None
    kind = "video" if tweet.media.videos or tweet.media.animated else "image" if tweet.media.photos else "text"
    return SocialContent(
        source_id=tweet.id_str,
        platform="x",
        url=validate_source_url("x", tweet.id_str, tweet.url),
        title=_text(tweet.rawContent, 300) or "X 帖子",
        author=_text(tweet.user.displayname, 160),
        published_at=tweet.date,
        body=_text(tweet.rawContent),
        collected_at=collected_at,
        content_type=kind,
    )


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


class FreeSocialClient:
    def __init__(self, *, x_cookies: str = "", xhs_base_url: str = "", xhs_auth_token: str = "") -> None:
        self._x_cookies = parse_x_search_cookies(x_cookies)
        self._xhs = (
            XiaohongshuServiceClient(base_url=xhs_base_url, auth_token=xhs_auth_token) if xhs_base_url.strip() else None
        )
        self._xhs_ready: bool | None = None
        self._xhs_ready_lock = asyncio.Lock()
        self._x_signer: XClIdGen | None = None
        self._x_signer_lock = asyncio.Lock()

    @property
    def supported_platforms(self) -> tuple[SocialPlatform, ...]:
        return tuple(
            platform
            for platform, configured in (("x", bool(self._x_cookies)), ("xiaohongshu", self._xhs is not None))
            if configured
        )

    async def _x_transaction_id(self, path: str, *, before_request: RequestGuard | None) -> str:
        async with self._x_signer_lock:
            if self._x_signer is None:
                cookies = httpx.Cookies()
                for key, value in self._x_cookies.items():
                    cookies.set(key, value, domain=".x.com")
                async with safe_outbound_async_client(
                    timeout=45,
                    follow_redirects=False,
                    cookies=cookies,
                    headers={"user-agent": _USER_AGENT},
                ) as client:
                    bootstrap = _XBootstrapClient(client, before_request)
                    try:
                        text = await get_tw_page_text("https://x.com", bootstrap)
                        vk_bytes, anim_key = await load_keys(BeautifulSoup(text, "html.parser"), bootstrap)
                        if bootstrap.guard_error is not None:
                            raise bootstrap.guard_error
                        self._x_signer = XClIdGen(vk_bytes, anim_key)
                    except Exception as exc:
                        if bootstrap.guard_error is not None:
                            raise bootstrap.guard_error
                        if isinstance(exc, SocialSourceError):
                            raise
                        if isinstance(exc, XClIdAccountError):
                            raise SocialSourceError(
                                "X operator login is required for signing",
                                code=401,
                                reason="login_required",
                            ) from None
                        if isinstance(exc, httpx.HTTPError):
                            raise SocialSourceError(
                                "X signing bootstrap network error",
                                reason="network_error",
                            ) from None
                        if isinstance(exc, (XClIdParseError, ValueError, TypeError, IndexError, KeyError)):
                            raise SocialSourceError(
                                "X signing bootstrap could not be parsed",
                                code=422,
                                reason="bootstrap_failed",
                            ) from None
                        raise
            return self._x_signer.calc("GET", path)

    async def _x_request(
        self,
        operation: str,
        variables: dict[str, Any],
        *,
        before_request: RequestGuard | None,
    ) -> dict[str, Any]:
        if not self._x_cookies:
            raise SocialSourceError("X search cookies are not configured", code=400, reason="unconfigured")
        params = encode_params({"variables": variables, "features": dict(GQL_FEATURES)})
        transaction_id = await self._x_transaction_id(f"/i/api/graphql/{operation}", before_request=before_request)
        headers = {
            "authorization": TOKEN,
            "x-csrf-token": self._x_cookies["ct0"],
            "x-twitter-auth-type": "OAuth2Session",
            "x-twitter-active-user": "yes",
            "x-twitter-client-language": "en",
            "user-agent": _USER_AGENT,
            "content-type": "application/json",
            "x-client-transaction-id": transaction_id,
        }
        if before_request is not None:
            await before_request()
        try:
            async with safe_outbound_async_client(timeout=45, follow_redirects=False) as client:
                response = await client.get(
                    f"{_X_GRAPHQL_URL}/{operation}",
                    params=params,
                    headers=headers,
                    cookies=self._x_cookies,
                )
                if not 200 <= response.status_code < 300:
                    raise SocialSourceError(
                        "X source request failed",
                        code=response.status_code,
                        reason=failure_reason(response.status_code),
                    )
                payload = response.json()
        except httpx.HTTPError:
            raise SocialSourceError("X source network error", reason="network_error") from None
        except ValueError:
            raise SocialSourceError("X returned an invalid response", code=422, reason="response_invalid") from None
        if isinstance(payload, dict) and payload.get("errors"):
            errors = payload["errors"] if isinstance(payload["errors"], list) else []
            codes = {error["code"] for error in errors if isinstance(error, dict) and type(error.get("code")) is int}
            if codes.intersection({32, 89, 215, 220, 239, 326}):
                raise SocialSourceError("X login cookies are no longer usable", code=401, reason="login_required")
            if 88 in codes:
                raise SocialSourceError("X source is rate limited", code=429, reason="rate_limited")
            raise SocialSourceError("X rejected the source query", code=422, reason="response_invalid")
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
            raise SocialSourceError("X returned no timeline data", code=422, reason="response_invalid")
        return payload

    async def _require_xhs_login(self, *, before_request: RequestGuard | None) -> XiaohongshuServiceClient:
        if self._xhs is None:
            raise SocialSourceError("Xiaohongshu source is not configured", code=400, reason="unconfigured")
        async with self._xhs_ready_lock:
            if self._xhs_ready is None:
                status = await self._xhs.status(before_request=before_request)
                self._xhs_ready = status.is_logged_in
            if not self._xhs_ready:
                raise SocialSourceError("Xiaohongshu operator login is required", code=401, reason="login_required")
        return self._xhs

    async def search(
        self,
        platform: SocialPlatform,
        query: str,
        *,
        window_start: datetime,
        window_end: datetime,
        cursor: dict[str, Any] | None = None,
        before_request: RequestGuard | None = None,
    ) -> SocialSearchPage:
        if platform not in self.supported_platforms:
            raise SocialSourceError("Social platform is not configured", code=400, reason="unsupported_platform")
        if platform == "x":
            end_date = window_end.date() + (
                timedelta(days=1) if window_end.time() != datetime.min.time() else timedelta()
            )
            raw_query = f"{query} since:{window_start.date().isoformat()} until:{end_date.isoformat()}"
            variables = {"rawQuery": raw_query, "count": 20, "product": "Latest", "querySource": "typed_query"}
            if cursor and cursor.get("cursor"):
                variables["cursor"] = str(cursor["cursor"])
            raw = await self._x_request(OP_SearchTimeline, variables, before_request=before_request)
            timeline = _mapping(
                _mapping(_mapping(raw["data"].get("search_by_raw_query")).get("search_timeline")).get("timeline"),
            )
            if not isinstance(timeline.get("instructions"), list):
                raise SocialSourceError("X search timeline shape changed", code=422, reason="response_invalid")
            try:
                tweets = list(parse_tweets(raw))
            except (KeyError, TypeError, ValueError) as exc:
                raise SocialSourceError(
                    "X source content could not be parsed",
                    code=422,
                    reason="parser_failed",
                ) from exc
            if not tweets and find_obj(raw, lambda item: item.get("__typename") == "Tweet") is not None:
                raise SocialSourceError("X source content could not be parsed", code=422, reason="parser_failed")
            try:
                sources = [_tweet_source(tweet, utc_now()) for tweet in tweets]
            except (AttributeError, TypeError, ValueError) as exc:
                raise SocialSourceError(
                    "X source content could not be normalized",
                    code=422,
                    reason="parser_failed",
                ) from exc
            candidates = [
                source
                for source in sources
                if source is not None
                and source.published_at is not None
                and window_start <= source.published_at < window_end
            ]
            next_item = find_obj(raw, lambda item: item.get("cursorType") == "Bottom")
            next_value = _text(next_item.get("value")) if isinstance(next_item, dict) else ""
            return SocialSearchPage(candidates=candidates, next_cursor={"cursor": next_value} if next_value else None)
        if cursor:
            raise SocialSourceError(
                "Xiaohongshu source does not support paginated search",
                code=400,
                reason="unsupported_platform",
            )
        xhs = await self._require_xhs_login(before_request=before_request)
        data = await xhs.search_feeds(query, before_request=before_request)
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
        if candidate.platform not in self.supported_platforms:
            raise SocialSourceError("Social platform is not configured", code=400, reason="unsupported_platform")
        if candidate.platform == "x":
            if not candidate.source_id.isdecimal():
                raise SocialSourceError("Invalid X content ID", code=422, reason="response_invalid")
            variables = {
                "focalTweetId": candidate.source_id,
                "with_rux_injections": True,
                "includePromotedContent": True,
                "withCommunity": True,
                "withQuickPromoteEligibilityTweetFields": True,
                "withBirdwatchNotes": True,
                "withVoice": True,
                "withV2Timeline": True,
            }
            raw = await self._x_request(OP_TweetDetail, variables, before_request=before_request)
            try:
                tweet = parse_tweet(raw, int(candidate.source_id))
            except (KeyError, TypeError, ValueError) as exc:
                raise SocialSourceError(
                    "X source content could not be parsed",
                    code=422,
                    reason="parser_failed",
                ) from exc
            if (
                tweet is None
                and find_obj(
                    raw,
                    lambda item: item.get("__typename") == "Tweet" and item.get("rest_id") == candidate.source_id,
                )
                is not None
            ):
                raise SocialSourceError("X source content could not be parsed", code=422, reason="parser_failed")
            try:
                return _tweet_source(tweet, utc_now()) if tweet is not None else None
            except (AttributeError, TypeError, ValueError) as exc:
                raise SocialSourceError(
                    "X source content could not be normalized",
                    code=422,
                    reason="parser_failed",
                ) from exc
        try:
            token = parse_qs(
                urlsplit(validate_source_url(candidate.platform, candidate.source_id, candidate.url)).query,
            ).get("xsec_token", [""])[0]
        except ValueError:
            raise SocialSourceError("Xiaohongshu source URL is invalid", code=422, reason="response_invalid") from None
        if not token:
            raise SocialSourceError("Xiaohongshu source token is missing", code=422, reason="response_invalid")
        xhs = await self._require_xhs_login(before_request=before_request)
        data = await xhs.feed_detail(candidate.source_id, token, before_request=before_request)
        note = _mapping(_mapping(data.get("data")).get("note"))
        if note.get("noteId") != candidate.source_id:
            raise SocialSourceError("Xiaohongshu detail does not match the source", code=422, reason="response_invalid")
        return _xhs_source(note, source_id=candidate.source_id, token=token, collected_at=utc_now(), detail=True)
