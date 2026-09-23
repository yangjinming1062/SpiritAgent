from ..base import ProviderError


def raise_for_qwen_response(resp, *, family: str, model: str) -> dict:
    """把 千问 HTTP 响应翻译为 dict 或 ProviderError。

    成功体用空 `code`/`message` + `output`；失败时顶层 `code`/`message` 非空。
    与 OpenAI 的 `error` 信封不同，不能直接走 `raise_for_provider_response`。
    """
    try:
        body = resp.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {"raw": str(body)} if body else {}

    code = body.get("code") or ""
    message = body.get("message") or body.get("detail") or ""
    # HTTP 200 但业务 code 非空时，用 body.status_code（缺省 502）避免被当成可重试 2xx
    status_code = resp.status_code
    if code and status_code < 400:
        try:
            status_code = int(body.get("status_code") or 502)
        except (TypeError, ValueError):
            status_code = 502
    if code or status_code >= 400:
        msg = f"{family} error {code or status_code}: {message or body}"
        raise ProviderError(msg, status_code=status_code, body=body, provider=family, model=model)
    return body
