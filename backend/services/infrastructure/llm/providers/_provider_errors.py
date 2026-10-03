from html.parser import HTMLParser

import httpx
from components import redact_sensitive_text

from .base import ProviderError


class _ErrorPageText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.hidden_tag: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.hidden_tag = tag

    def handle_endtag(self, tag: str) -> None:
        if tag == self.hidden_tag:
            self.hidden_tag = None

    def handle_data(self, data: str) -> None:
        if self.hidden_tag is None:
            self.parts.append(data)


def _non_json_error(resp: httpx.Response) -> dict:
    content_type = resp.headers.get("content-type", "").partition(";")[0].strip().lower()
    metadata = {"content_type": content_type or "unknown", "body_bytes": len(resp.content)}
    sample = resp.content[: 64 * 1024]
    text = sample.decode("utf-8", errors="replace")
    if content_type == "text/html" or text.lstrip().lower().startswith(("<!doctype html", "<html")):
        parser = _ErrorPageText()
        parser.feed(text)
        text = " ".join(parser.parts)
    elif not content_type.startswith("text/") and (
        content_type
        or any(character == "\ufffd" or (ord(character) < 32 and not character.isspace()) for character in text)
    ):
        return metadata
    diagnostic = redact_sensitive_text(" ".join(text.split())) or ""
    return {**metadata, "raw": diagnostic[:500]} if diagnostic else metadata


def response_json(resp: httpx.Response) -> dict:
    """解析对象响应；错误正文保留有界、脱敏的文本或二进制元信息。"""
    try:
        body = resp.json()
    except ValueError:
        return _non_json_error(resp) if resp.status_code >= 400 else {}
    if isinstance(body, dict):
        return body
    return {"raw": str(body)} if body else {}


def _format_err(family: str, err: object) -> str:
    """把供应商错误体渲染为单行字符串；容忍 dict/str/任意嵌套形态。"""
    if isinstance(err, str):
        return f"{family} error: {err}"
    if isinstance(err, dict):
        code = err.get("code", "?")
        msg = err.get("message", err.get("detail", ""))
        return f"{family} error {code}: {msg}" if msg else f"{family} error {code}"
    return f"{family} error: {err!r}"


def raise_for_provider_response(resp: httpx.Response, *, family: str) -> dict:
    """OpenAI 形态错误信封（``{"error": {...}}``）：返回 dict 体或抛 ProviderError。"""
    body = response_json(resp)
    if err := body.get("error"):
        msg = _format_err(family, err)
    elif resp.status_code >= 400:
        msg = f"{family} HTTP {resp.status_code}: {body}"
    else:
        return body
    raise ProviderError(msg, status_code=resp.status_code, body=body)
