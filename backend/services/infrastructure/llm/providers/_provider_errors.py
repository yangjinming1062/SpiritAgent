import httpx

from .base import ProviderError


def response_json(resp: httpx.Response) -> dict:
    """解析响应 JSON 体；非 JSON 为空 dict，非对象 JSON 包装为 ``{"raw": ...}``。"""
    try:
        body = resp.json()
    except Exception:
        return {}
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
