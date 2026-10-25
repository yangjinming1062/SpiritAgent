"""账户独立的 X 读取适配器；只复用 SDK 的固定请求定义与解析器。"""

import asyncio
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup
from twscrape.account import TOKEN
from twscrape.api import GQL_FEATURES, OP_SearchTimeline, OP_TweetDetail
from twscrape.models import Tweet, parse_tweet, parse_tweets
from twscrape.utils import encode_params, find_obj
from twscrape.xclid import XClIdAccountError, XClIdGen, XClIdParseError, get_tw_page_text, load_keys

from infrastructure.network import safe_outbound_async_client
from infrastructure.text import utc_now

from .social_content import (
    RequestGuard,
    SocialContent,
    SocialSearchPage,
    SocialSourceError,
    failure_reason,
    validate_source_url,
)
from .social_credentials import parse_x_search_cookies
from .social_parsing import mapping as _mapping
from .social_parsing import text as _text

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


class XSource:
    def __init__(self, *, cookies: str) -> None:
        self._x_cookies = parse_x_search_cookies(cookies)
        self._x_signer: XClIdGen | None = None
        self._x_signer_lock = asyncio.Lock()

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
                                "X account login is required for signing",
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

    async def search(
        self,
        query: str,
        *,
        window_start: datetime,
        window_end: datetime,
        cursor: dict[str, Any] | None = None,
        before_request: RequestGuard | None = None,
    ) -> SocialSearchPage:
        end_date = window_end.date() + (timedelta(days=1) if window_end.time() != datetime.min.time() else timedelta())
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

    async def detail(
        self,
        candidate: SocialContent,
        *,
        before_request: RequestGuard | None = None,
    ) -> SocialContent | None:
        if candidate.platform != "x":
            raise SocialSourceError("Unexpected source platform", code=400, reason="unsupported_platform")
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
