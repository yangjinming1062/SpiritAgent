import httpx

from ..base import ProviderError

# xAI 视频端点失败时返回 error: "<string>"，不符合共享 raise_for_provider_response 假设的 OpenAI 形态 {"code","message"}；解析器独立成模块（与 minimax/_errors.py 保持同一约定），后续遇到非 OpenAI 错误形态的供应商应平行新增模块。


def raise_for_grok_response(resp: httpx.Response, *, provider: str, model: str) -> dict:
    try:
        body = resp.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {"raw": str(body)} if body else {}
    if resp.status_code >= 400:
        err = body.get("error")
        msg = f"grok {provider} HTTP {resp.status_code}: {err if err else body}"
        raise ProviderError(msg, status_code=resp.status_code, body=body, provider=provider, model=model)
    return body
