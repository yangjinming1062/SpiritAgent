"""see-through 拆分客户端 — HF Space 主用、魔搭 ModelScope 备用，单图拆分为分层 PSD。

双 provider 同构 Gradio 协议（upload → call → SSE 轮询）。主用任何失败自动切备用
（免费资源，多试一次成本为零）；主用确认每日限额后进程内冷却 6 小时直接走备用。
两路都失败抛 SeeThroughError，由调用方落失败态（无 CPU 兜底链）。"""

import json
import time
from typing import Any
from urllib.parse import urlparse

import httpx
from components import SETTINGS, get_logger

logger = get_logger(__name__)

_TOTAL_TIMEOUT_SECONDS = 900.0
_MIN_PSD_BYTES = 10240
# 双 provider 最坏 900s+900s 会撞 outfit 拆分 30 分钟清扫窗口；共享预算留余量
_TOTAL_BUDGET_SECONDS = 1740.0
_QUOTA_COOLDOWN_SECONDS = 6 * 3600.0
_QUOTA_SIGNALS = ("quota", "exceeded", "rate limit", "too many", "sign in", "daily")

_primary_quota_until = 0.0


class SeeThroughError(RuntimeError):
    """拆分失败。kind：quota（每日限额）/ transport（网络）/ space（其余 Space 侧错误）。"""

    def __init__(self, message: str, *, kind: str = "space") -> None:
        super().__init__(message)
        self.kind = kind


def _classify_error_text(text: str) -> str:
    lowered = text.lower()
    return "quota" if any(signal in lowered for signal in _QUOTA_SIGNALS) else "space"


def _provider_file_url(base: str, url: str) -> str:
    """魔搭 complete 载荷的文件 URL 落在 ms.show 运行域（带 Bearer 反而 403）；
    统一改写到 provider base 域——api-inference 代理 /gradio_api/file= 且接受同一 token。"""
    base_parts = urlparse(base)
    url_parts = urlparse(url)

    if url_parts.netloc == base_parts.netloc:
        return url

    return url_parts._replace(scheme=base_parts.scheme, netloc=base_parts.netloc).geturl()


async def split_to_psd(
    image_bytes: bytes,
    *,
    mime: str = "image/jpeg",
    resolution: int = 1024,
    seed: int = 0,
    tblr_split: bool = True,
) -> bytes:
    """主用 HF、备用魔搭各试一次（单 provider 均不重试，额度保护）；返回 PSD 字节。"""
    global _primary_quota_until
    deadline = time.monotonic() + _TOTAL_BUDGET_SECONDS
    fallback_base = SETTINGS.seethrough_fallback_base or None
    fallback_headers: dict[str, str] = {}
    if SETTINGS.seethrough_fallback_token:
        fallback_headers["Authorization"] = f"Bearer {SETTINGS.seethrough_fallback_token}"
    primary_reason = "skipped: quota cooldown"

    async with httpx.AsyncClient(timeout=httpx.Timeout(_TOTAL_TIMEOUT_SECONDS, connect=30.0)) as client:
        if time.monotonic() >= _primary_quota_until:
            try:
                psd = await _attempt_split(
                    client,
                    SETTINGS.seethrough_space_base,
                    image_bytes,
                    mime,
                    resolution,
                    seed,
                    tblr_split,
                )
                logger.info("2d split provider succeeded: hf")
                return psd
            except SeeThroughError as exc:
                primary_reason = str(exc)
                if exc.kind == "quota":
                    _primary_quota_until = time.monotonic() + _QUOTA_COOLDOWN_SECONDS
                logger.warning("2d split provider failed: hf", extra={"error": primary_reason, "kind": exc.kind})
        else:
            logger.info("hf quota cooldown active; going straight to fallback")

        if not fallback_base:
            raise SeeThroughError(f"2d split failed (hf: {primary_reason}; fallback disabled)")

        if deadline - time.monotonic() < 60.0:
            raise SeeThroughError(
                f"2d split failed on all providers (hf: {primary_reason}; modelscope: skipped, budget exhausted)",
            )

        try:
            psd = await _attempt_split(
                client,
                fallback_base,
                image_bytes,
                mime,
                resolution,
                seed,
                tblr_split,
                headers=fallback_headers,
            )
            logger.info("2d split provider succeeded: modelscope")
            return psd
        except SeeThroughError as exc:
            raise SeeThroughError(
                f"2d split failed on all providers (hf: {primary_reason}; modelscope: {exc})",
            ) from exc


async def _attempt_split(
    client: httpx.AsyncClient,
    base: str,
    image_bytes: bytes,
    mime: str,
    resolution: int,
    seed: int,
    tblr_split: bool,
    *,
    headers: dict[str, str] | None = None,
) -> bytes:
    try:
        file_data = await _upload(client, base, image_bytes, mime, headers)
        event_id = await _submit(client, base, file_data, resolution, seed, tblr_split, headers)
        psd_url = _provider_file_url(base, await _wait_complete(client, base, event_id, headers))
        return await _download(client, psd_url, headers)
    except SeeThroughError:
        raise
    except httpx.TransportError as exc:
        raise SeeThroughError(f"see-through transport failed: {exc}", kind="transport") from exc
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        kind = "quota" if status in (429, 402) else "space"
        raise SeeThroughError(f"see-through http {status}: {exc}", kind=kind) from exc
    except Exception as exc:
        raise SeeThroughError(f"see-through transport failed: {exc}") from exc


async def _upload(
    client: httpx.AsyncClient,
    base: str,
    image_bytes: bytes,
    mime: str,
    headers: dict[str, str] | None,
) -> dict[str, Any]:
    ext = "png" if "png" in mime else "jpg"
    resp = await client.post(f"{base}/upload", files={"files": (f"seed.{ext}", image_bytes, mime)}, headers=headers)

    resp.raise_for_status()
    paths = resp.json()

    if not isinstance(paths, list) or not paths:
        raise SeeThroughError("upload returned no file handle")

    # 参数必须传 FileData 对象而非裸路径字符串——后者被新版 Gradio 拒收（event:error null）
    return {"path": paths[0], "meta": {"_type": "gradio.FileData"}}


async def _submit(
    client: httpx.AsyncClient,
    base: str,
    file_data: dict[str, Any],
    resolution: int,
    seed: int,
    tblr_split: bool,
    headers: dict[str, str] | None,
) -> str:
    resp = await client.post(
        f"{base}/call/inference",
        json={"data": [file_data, resolution, seed, tblr_split]},
        headers=headers,
    )
    resp.raise_for_status()
    event_id = resp.json().get("event_id")

    if not event_id:
        raise SeeThroughError("submit returned no event_id")

    return event_id


async def _wait_complete(
    client: httpx.AsyncClient,
    base: str,
    event_id: str,
    headers: dict[str, str] | None,
) -> str:
    """SSE 轮询直到 complete 事件；error 事件与流中断都转 SeeThroughError。"""
    event = ""

    async with client.stream("GET", f"{base}/call/inference/{event_id}", headers=headers) as stream:
        async for line in stream.aiter_lines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:") and event:
                data = line[5:].strip()

                if event == "error":
                    raise SeeThroughError(
                        f"space reported error: {data[:200]}",
                        kind=_classify_error_text(data),
                    )

                if event == "complete":
                    return _extract_psd_url(data)

    raise SeeThroughError("sse stream ended without complete event")


def _extract_psd_url(data: str) -> str:
    payload = json.loads(data)
    first = payload[0] if isinstance(payload, list) and payload else None
    url = first.get("url") if isinstance(first, dict) else None

    if not url:
        raise SeeThroughError("complete payload carries no psd url")

    return url


async def _download(client: httpx.AsyncClient, url: str, headers: dict[str, str] | None) -> bytes:
    resp = await client.get(url, follow_redirects=True, headers=headers)
    resp.raise_for_status()

    if len(resp.content) < _MIN_PSD_BYTES:
        raise SeeThroughError(f"psd suspiciously small: {len(resp.content)} bytes")

    return resp.content
