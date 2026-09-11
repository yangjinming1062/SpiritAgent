import base64
import json

from ..base import ProviderError

# MiniMax 用 base_resp.status_code 承载自有错误分类（叠加在 HTTP 状态码之上）；下表映射使 error_classifier.classify_api_error 与同条件 OpenAI 错误落到同一 FailoverReason 桶。
_BASE_RESP_TO_HTTP: dict[int, int] = {
    1002: 429,  # rate limit
    1004: 401,  # auth
    1008: 402,  # billing
    1013: 400,  # bad params
    1026: 400,  # content moderation — 敏感输入，下游按消息关键字分类
    1027: 400,  # content filter — 下游按消息关键字分类
    1039: 429,  # concurrency / quota
    2013: 400,  # invalid param
}

# MiniMax 把套餐/额度拒单归到通用 invalid-param 码下，单凭内部码无法与真正格式错误区分。
_ENTITLEMENT_SIGNALS = ("tokenplan", "credit")


def _raise_for_base_resp(base: dict, *, provider: str, model: str) -> None:
    raw_inner_code = base.get("status_code", 0)
    inner_msg = base.get("status_msg", "") or ""
    if not raw_inner_code or raw_inner_code == 0:
        return
    try:
        inner_int = int(raw_inner_code)
    except (TypeError, ValueError):
        raise ProviderError(
            f"minimax {provider} non-numeric status_code: {raw_inner_code!r}",
            status_code=502,
            provider=provider,
            model=model,
        ) from None
    if inner_int in (1013, 2013) and any(s in inner_msg.lower() for s in _ENTITLEMENT_SIGNALS):
        http = 402
    elif inner_int in _BASE_RESP_TO_HTTP:
        http = _BASE_RESP_TO_HTTP[inner_int]
    else:
        # 不回退到常为 200 的 resp.status_code；统一以 502 上抛，error_classifier 标记为可重试。
        http = 502
    extra_body = {"error": {"code": str(raw_inner_code), "message": inner_msg}, "base_resp": base}
    raise ProviderError(
        f"minimax {provider} error {raw_inner_code}: {inner_msg}",
        status_code=http,
        body=extra_body,
        provider=provider,
        model=model,
    )


def raise_for_minimax_response(resp, *, provider: str, model: str) -> dict:
    """把 MiniMax HTTP 响应翻译为 dict 体或抛字段对齐 classify_api_error 的 ProviderError；MiniMax 把错误信息裹在 {"base_resp":{"status_code":N,"status_msg":"..."},"data":null}，且 HTTP 状态常为 200 即便 base_resp.status_code≠0；HTTP 4xx/5xx 自带另一 JSON 形态，本函数两种都处理；已知内部码走 _BASE_RESP_TO_HTTP，未知码映射为 502（避免回退到常为 200 的 resp.status_code）；非数值的非零 base_resp.status_code（如网关返回 "SYSTEM_ERROR"）抛 502 ProviderError（避免 inner_int=0 静默成功）。"""
    try:
        body = resp.json()
    except (json.JSONDecodeError, ValueError):
        body = {}

    if isinstance(body, dict) and "base_resp" in body:
        _raise_for_base_resp(body.get("base_resp") or {}, provider=provider, model=model)
        return body

    if resp.status_code >= 400:
        raise ProviderError(
            f"minimax {provider} HTTP {resp.status_code}: {body}",
            status_code=resp.status_code,
            body=body if isinstance(body, dict) else {"raw": str(body)},
            provider=provider,
            model=model,
        )
    return body if isinstance(body, dict) else {}


def raise_for_minimax_stream_event(body: dict, *, provider: str, model: str) -> None:
    """SSE 流式事件的 base_resp 校验——错误同样裹在 200 事件体内（首事件前抛出仍可回退下一家）。"""
    base = body.get("base_resp")
    if isinstance(base, dict):
        _raise_for_base_resp(base, provider=provider, model=model)


def extract_minimax_audio(body: dict) -> bytes:
    data = body.get("data") or {}
    audio_hex = data.get("audio") or ""
    if audio_hex:
        return bytes.fromhex(audio_hex)
    audio_b64 = data.get("audio_base64") or ""
    if audio_b64:
        return base64.b64decode(audio_b64)
    raise RuntimeError("MiniMax TTS response contained no audio payload")
