"""供应商视频成品的公共接收：下载、格式核对与确定性路径落盘。"""

from collections.abc import Awaitable, Callable

from infrastructure.ai.error_classifier import LLMRuntimeError
from infrastructure.assets.asset_store import (
    resolve_asset_reference,
    save_video_job_asset_async,
    sniff_media_ext,
    video_job_asset_path,
)
from infrastructure.assets.media_download import download_media_result
from infrastructure.config import SETTINGS

from .chain import MediaChainState, media_failure_reason, video_provider_failure_reason
from .video_queue.contracts import VideoQueueInfo, VideoQueueStoppedError
from .video_queue.service import acknowledge_video_attempt, wait_video_attempt

DownloadGuard = Callable[[], Awaitable[None]]
VideoProgressWriter = Callable[[MediaChainState], Awaitable[None]]


async def receive_video_attempt(
    chain: MediaChainState,
    save_progress: VideoProgressWriter,
    *,
    on_queue: Callable[[VideoQueueInfo | None], Awaitable[None]] | None = None,
) -> str | None:
    """接收原尝试终态，先持久化接续点再确认消费；只在确定失败后开放链尾。"""
    request_id = chain.queue_request_id
    if request_id is None:
        raise ValueError("视频任务缺少供应商尝试")
    reason = None
    can_fallback = False
    try:
        status = await wait_video_attempt(request_id, on_queue=on_queue)
    except (LLMRuntimeError, VideoQueueStoppedError) as exc:
        reason, can_fallback = media_failure_reason(exc)
        if reason == "result_unknown":
            raise
    else:
        if status.status == "failed":
            reason, can_fallback = video_provider_failure_reason(status.error), True
        elif status.status == "succeeded" and status.download_url:
            chain.result_url = status.download_url
        else:
            raise RuntimeError("视频队列未返回可接收的终态结果")
    chain.queue_request_id = None
    if reason:
        chain.phase, chain.active_index = "ready", None
        if not can_fallback or chain.next_index >= len(chain.providers):
            chain.stop_reason = reason
    else:
        chain.phase = "storing"
    await save_progress(chain)
    await acknowledge_video_attempt(request_id)
    return reason


async def download_and_store_video(
    download_url: str | None,
    *,
    user_id: int,
    job_id: int,
    attempt: int,
    generation_id: str,
    directory: str,
    guard: DownloadGuard | None = None,
) -> str:
    """有界重试下载供应商成品并原子写入确定性路径，返回裸存储路径。

    只对传输错误与服务端 5xx 退避重试（``download_media_result`` 内建）；
    大小上限由 ``video_gen_download_max_bytes`` 定义；守卫在下载前后核对业务任务仍存活，
    返回非 mp4 载荷视为供应商失败。已知地址下载失败由调用方保留原结果继续接收，不重新提交。
    """
    if not download_url:
        raise RuntimeError("video result has no download url")
    if guard is not None:
        await guard()
    path = video_job_asset_path(user_id, job_id, attempt, generation_id=generation_id, directory=directory)
    stored = resolve_asset_reference(path)
    if stored is not None and stored[0].is_file():
        return path
    data = await download_media_result(download_url, max_bytes=SETTINGS.video_gen_download_max_bytes)
    if guard is not None:
        await guard()
    if sniff_media_ext(data) != "mp4":
        raise RuntimeError("provider returned a payload that is not an mp4 stream")
    return await save_video_job_asset_async(
        data,
        user_id=user_id,
        job_id=job_id,
        attempt=attempt,
        generation_id=generation_id,
        directory=directory,
    )
