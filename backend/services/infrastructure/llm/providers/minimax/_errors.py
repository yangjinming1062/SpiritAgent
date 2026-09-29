import base64

import httpx

from .._provider_errors import response_json
from ..base import ProviderError

# MiniMax 用 base_resp.status_code 承载自有错误分类（叠加在 HTTP 状态码之上）；映射为 HTTP 状态，使错误分类与同条件 OpenAI 错误一致。
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


def _raise_for_base_resp(base: dict) -> None:
    raw_inner_code = base.get("status_code", 0)
    inner_msg = base.get("status_msg", "") or ""
    if not raw_inner_code:
        return
    try:
        inner_int = int(raw_inner_code)
    except (TypeError, ValueError):
        raise ProviderError(f"minimax non-numeric status_code: {raw_inner_code!r}", status_code=502) from None
    if inner_int in (1013, 2013) and any(s in inner_msg.lower() for s in _ENTITLEMENT_SIGNALS):
        http = 402
    else:
        # 未知码不回退到常为 200 的 resp.status_code；统一以 502 上抛，按服务端错误处理。
        http = _BASE_RESP_TO_HTTP.get(inner_int, 502)
    raise ProviderError(
        f"minimax error {raw_inner_code}: {inner_msg}",
        status_code=http,
        body={"error": {"code": str(raw_inner_code), "message": inner_msg}, "base_resp": base},
    )


def raise_for_minimax_response(resp: httpx.Response) -> dict:
    """把 MiniMax HTTP 响应翻译为 dict 体或抛 ProviderError。

    MiniMax 把错误裹在 ``{"base_resp":{"status_code":N,"status_msg":"..."}}``，HTTP 状态常为 200；
    HTTP 4xx/5xx 另有 JSON 形态，两种都处理。非零 base_resp 码（含非数值码）一律抛错，不静默成功。
    """
    body = response_json(resp)
    if "base_resp" in body:
        _raise_for_base_resp(body.get("base_resp") or {})
        return body
    if resp.status_code >= 400:
        raise ProviderError(f"minimax HTTP {resp.status_code}: {body}", status_code=resp.status_code, body=body)
    return body


def extract_minimax_audio(body: dict) -> bytes:
    data = body.get("data") or {}
    audio_hex = data.get("audio") or ""
    if audio_hex:
        return bytes.fromhex(audio_hex)
    audio_b64 = data.get("audio_base64") or ""
    if audio_b64:
        return base64.b64decode(audio_b64)
    raise RuntimeError("MiniMax TTS response contained no audio payload")
