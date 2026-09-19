"""see-through 拆分客户端 — HF Space 主用、魔搭 ModelScope 备用，单图拆分为分层 PSD。

双 provider 同构 Gradio 协议（upload → call → SSE 轮询）。主用任何失败自动切备用
（免费资源，多试一次成本为零）；主用确认每日限额后进程内冷却 6 小时直接走备用。
两路都失败抛 SeeThroughError，由调用方落失败态（无 CPU 兜底链）。

社区算力休眠、排队与推理耗时不可控：提交、推理、下载三段各有独立超时（SETTINGS 热调），
再受总预算约束——不用单个大而全的读超时掩盖阶段停滞。提交 MIME 按文件魔数探测；
下载结果只做 PSD 签名与大小校验，外观是否合格由调用方（mesh2d.appearance）
以原图锁定重建 + 门禁判定，本模块不以“下载成功”代替外观合格。"""

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx
from components import SETTINGS, get_logger

logger = get_logger(__name__)

_MIN_PSD_BYTES = 10240
_PSD_SIGNATURE = b"8BPS"
# 主用耗尽预算后，备用路至少要留这么多秒，否则直接跳过（唤醒都来不及）。
_FALLBACK_MIN_BUDGET_SECONDS = 60.0
_QUOTA_COOLDOWN_SECONDS = 6 * 3600.0
_QUOTA_SIGNALS = ("quota", "exceeded", "rate limit", "too many", "sign in", "daily")

_primary_quota_until = 0.0


def _sniff_image_mime(data: bytes) -> str:
    """按魔数判定提交 MIME；仅接受 PNG/JPEG，其余直接报错，避免服务端按错误类型解码。"""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    raise SeeThroughError("unsupported source image type: expected PNG or JPEG")


class SeeThroughError(RuntimeError):
    """拆分失败。kind：quota（每日限额）/ transport（网络）/ space（其余 Space 侧错误）。"""

    def __init__(self, message: str, *, kind: str = "space") -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class _StageTimeouts:
    """拆分各阶段的独立超时（秒）。"""

    submit: float
    inference: float
    download: float

    @classmethod
    def from_settings(cls) -> "_StageTimeouts":
        return cls(
            submit=SETTINGS.seethrough_submit_timeout_seconds,
            inference=SETTINGS.seethrough_inference_timeout_seconds,
            download=SETTINGS.seethrough_download_timeout_seconds,
        )


def _remaining(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


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
    mime: str | None = None,
    resolution: int = 1024,
    seed: int = 0,
    tblr_split: bool = True,
) -> bytes:
    """主用 HF、备用魔搭各试一次（单 provider 均不重试，额度保护）；返回 PSD 字节。

    mime 缺省时按文件魔数探测；返回内容仅是候选分层，调用方必须执行
    原图锁定重建与外观门禁后方可发布（mesh2d.appearance）。"""
    global _primary_quota_until
    mime = mime or _sniff_image_mime(image_bytes)
    deadline = time.monotonic() + SETTINGS.seethrough_total_budget_seconds
    stages = _StageTimeouts.from_settings()
    fallback_base = SETTINGS.seethrough_fallback_base or None
    fallback_headers: dict[str, str] = {}
    if SETTINGS.seethrough_fallback_token:
        fallback_headers["Authorization"] = f"Bearer {SETTINGS.seethrough_fallback_token}"
    primary_reason = "skipped: quota cooldown"

    async with httpx.AsyncClient() as client:
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
                    stages=stages,
                    deadline=deadline,
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

        if _remaining(deadline) < _FALLBACK_MIN_BUDGET_SECONDS:
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
                stages=stages,
                deadline=deadline,
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
    stages: _StageTimeouts,
    deadline: float,
) -> bytes:
    if _remaining(deadline) <= 0:
        raise SeeThroughError("budget exhausted before split start")
    stage = "submit"
    try:
        async with asyncio.timeout(_remaining(deadline)):
            async with asyncio.timeout(stages.submit):
                submit_timeout = httpx.Timeout(stages.submit, connect=min(30.0, stages.submit))
                file_data = await _upload(client, base, image_bytes, mime, headers, timeout=submit_timeout)
                event_id = await _submit(
                    client,
                    base,
                    file_data,
                    resolution,
                    seed,
                    tblr_split,
                    headers,
                    timeout=submit_timeout,
                )
            stage = "inference"
            async with asyncio.timeout(stages.inference):
                psd_url = _provider_file_url(
                    base,
                    await _wait_complete(client, base, event_id, headers, stage_limit=stages.inference),
                )
            stage = "download"
            async with asyncio.timeout(stages.download):
                return await _download(
                    client,
                    psd_url,
                    headers,
                    timeout=httpx.Timeout(stages.download, connect=min(30.0, stages.download)),
                )
    except TimeoutError as exc:
        raise SeeThroughError(f"{stage} stage or total budget timed out", kind="transport") from exc
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
    *,
    timeout: httpx.Timeout,
) -> dict[str, Any]:
    ext = "png" if "png" in mime else "jpg"
    resp = await client.post(
        f"{base}/upload",
        files={"files": (f"seed.{ext}", image_bytes, mime)},
        headers=headers,
        timeout=timeout,
    )

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
    *,
    timeout: httpx.Timeout,
) -> str:
    resp = await client.post(
        f"{base}/call/inference",
        json={"data": [file_data, resolution, seed, tblr_split]},
        headers=headers,
        timeout=timeout,
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
    *,
    stage_limit: float,
) -> str:
    """SSE 轮询直到 complete 事件；error 事件与流中断都转 SeeThroughError。

    阶段累计耗时与总预算由调用边界约束，HTTP 读超时仅约束停滞。
    """
    event = ""
    read_timeout = httpx.Timeout(stage_limit, connect=min(30.0, stage_limit))

    async with client.stream(
        "GET",
        f"{base}/call/inference/{event_id}",
        headers=headers,
        timeout=read_timeout,
    ) as stream:
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


async def _download(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str] | None,
    *,
    timeout: httpx.Timeout,
) -> bytes:
    resp = await client.get(url, follow_redirects=True, headers=headers, timeout=timeout)
    resp.raise_for_status()

    if not resp.content.startswith(_PSD_SIGNATURE):
        raise SeeThroughError("downloaded split result is not a PSD file")
    if len(resp.content) < _MIN_PSD_BYTES:
        raise SeeThroughError(f"psd suspiciously small: {len(resp.content)} bytes")

    return resp.content
