from .base import ProviderError


def _format_err(family: str, err) -> str:
    """把供应商错误体渲染为单行字符串；容忍 dict/str/None/任意嵌套形态。"""
    if not err:
        return f"{family} error (no detail)"
    if isinstance(err, str):
        return f"{family} error: {err}" if err else f"{family} error (empty)"
    if isinstance(err, dict):
        code = err.get("code", "?")
        msg = err.get("message", err.get("detail", ""))
        return f"{family} error {code}: {msg}" if msg else f"{family} error {code}"
    return f"{family} error: {err!r}"


def raise_for_provider_response(resp, *, family: str, model: str) -> dict:
    """把 HTTP 响应翻译为 dict 体或抛 ProviderError；``family`` 同时用作消息前缀与 ProviderError.provider 字段。"""
    try:
        body = resp.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {"raw": str(body)} if body else {}

    if err := body.get("error"):
        msg = _format_err(family, err)
    elif resp.status_code >= 400:
        msg = f"{family} HTTP {resp.status_code}: {body}"
    else:
        return body

    raise ProviderError(msg, status_code=resp.status_code, body=body, provider=family, model=model)
