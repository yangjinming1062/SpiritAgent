import httpx

from .._provider_errors import response_json
from ..base import ProviderError


# xAI 视频端点失败时返回 error: "<string>"，且失败任务的轮询结果为 2xx 并带 error 字段；只按 HTTP 状态判错。
def raise_for_grok_response(resp: httpx.Response) -> dict:
    body = response_json(resp)
    if resp.status_code >= 400:
        err = body.get("error")
        msg = f"grok video HTTP {resp.status_code}: {err if err else body}"
        raise ProviderError(msg, status_code=resp.status_code, body=body)
    return body
