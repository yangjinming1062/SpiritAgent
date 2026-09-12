"""图生 3D 模型能力链编排。

链拓扑（``task_id`` 串联,bytes 不跨 hop 传递）：submit → poll →
按 ``provider.SUPPORTS_RIGGING`` 可选 cloud_rig → 按 ``provider.SUPPORTS_ANIMATE_BIND``
可选 cloud_animate_bind → download → 落库。只有链末产物会下载,后端不解析、不校验、不二次处理。
"""

import asyncio
import json
import tempfile
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import httpx
from components import SESSION_LOCAL, SETTINGS, backoff_for_poll, get_logger, log_paid_call
from components.user_maintenance_runtime import track_user_task
from modules.companion import Companion3DModel
from modules.ws import emit_ws_event
from sqlalchemy import select, update

from services.domains.companion import select_rig_type
from services.infrastructure.assets.asset_store import (
    build_signed_model_url,
    get_companion_model_sha256,
    save_companion_model,
)
from services.infrastructure.image_to_3d import (
    ImageTo3DError,
    ImageTo3DProvider,
    Model3DAsset,
    Model3DJob,
    Model3DPollResult,
    resolve_provider,
)
from services.infrastructure.llm import chat

from .avatar_service import resolve_uploaded_avatar_path

logger = get_logger(__name__)

IN_FLIGHT_STATUSES: tuple[str, ...] = ("generating", "pending_download", "downloading")
RETRYABLE_DOWNLOAD_STATUSES: tuple[str, ...] = ("pending_download", "download_failed")

_DOWNLOAD_ATTEMPTS: int = 3
_DOWNLOAD_RETRY_BASE_DELAY: float = 2.0
_DOWNLOAD_URL_REFRESH_LIMIT: int = 2

# 各 phase 在前端进度条上的区间；resume 时复用同一区间以保持 UX 一致。
_PHASE_PROGRESS: dict[str, tuple[str, int, int]] = {
    "submit": ("generating", 10, 50),
    "rig": ("rigging", 90, 95),
    "animate": ("animate_binding", 92, 96),
}

# web 进程内 asyncio 任务句柄,``recover_stuck_model_generations`` + ``resume_inflight_pipelines`` 启动时负责回收与重派。
_inflight_tasks: dict[int, asyncio.Task] = {}

# 防止两个并发请求同时通过检查(TOCTOU)而起两条流水线
MODEL_JOB_LOCKS: dict[int, asyncio.Lock] = {}


class ModelGenerationError(RuntimeError):
    pass


class ModelProviderNotConfiguredError(ModelGenerationError):
    """无可用的 image-to-3D 供应商；客户端继续以精灵图模式运行。"""


class ModelGenerationInProgressError(ModelGenerationError):
    """该用户已有生成任务在跑；并发请求须在行级别拒绝。"""


def get_model_job_lock(user_id: int) -> asyncio.Lock:
    """惰性创建并返回用户级锁；条目不回收（锁很小且 user_id 空间有限）。"""
    return MODEL_JOB_LOCKS.setdefault(user_id, asyncio.Lock())


@dataclass
class _PipelineState:
    provider_task_id: str
    assets: tuple[Model3DAsset, ...]


def _resolve_model_provider(name: str | None) -> ImageTo3DProvider:
    """只按显式指定解析——商业供应商之间绝不互相故障转移。"""
    try:
        return resolve_provider(name)
    except (ImageTo3DError, LookupError) as exc:
        raise ModelProviderNotConfiguredError(str(exc)) from exc


def _provider_result_label(provider_name: str, multiview: bool = False) -> str:
    return f"{provider_name}_{'multiview' if multiview else 'image'}_to_3d"


def _raw_provider_name(label: str | None) -> str | None:
    """注册表键是结果标签的下划线首段。"""
    return label.split("_", 1)[0] if label else None


async def _emit_progress(user_id: int, stage: str, progress_pct: int, *, provider: str | None = None) -> None:
    payload: dict[str, str | int] = {"stage": stage, "progress": progress_pct}
    if provider:
        payload["provider"] = provider
    try:
        async with SESSION_LOCAL() as db:
            emit_ws_event(db, user_id=user_id, event_type="model.gen.progress", payload=payload)
            await db.commit()
    except Exception:
        logger.warning("Failed to emit model.gen.progress", exc_info=True)


async def _emit_model_ready(
    user_id: int,
    model_id: int,
    asset_url: str,
    *,
    species: str | None = None,
    rig_type: str | None = None,
    style: str | None = None,
    content_hash: str | None = None,
    clip_map: dict[str, str] | None = None,
) -> None:
    payload: dict[str, object] = {"model_id": model_id, "clip_map": clip_map or {}}
    if species:
        payload["species"] = species
    if rig_type:
        payload["rig_type"] = rig_type
    if style:
        payload["style"] = style
    parts = asset_url.split("/", 2)
    if len(parts) == 3:
        payload["asset_url"] = build_signed_model_url(int(parts[1]), parts[2])
        if not content_hash:
            content_hash = get_companion_model_sha256(int(parts[1]), parts[2])
    if content_hash:
        payload["content_hash"] = content_hash
    try:
        async with SESSION_LOCAL() as db:
            emit_ws_event(db, user_id=user_id, event_type="model.ready", payload=payload)
            await db.commit()
    except Exception:
        logger.warning("Failed to emit model.ready", exc_info=True)


async def _emit_model_failed(
    user_id: int,
    reason: str,
    *,
    retry_download: bool = False,
    model_id: int | None = None,
) -> None:
    payload: dict[str, object] = {"reason": reason}
    # retry_download=true 表示只是下载环节失败：付费结果仍在，客户端应提供重试下载而非付费重生成（docs/PROTOCOL.md §1.3）
    if retry_download:
        payload["retry_download"] = True
    if model_id is not None:
        payload["model_id"] = model_id
    try:
        async with SESSION_LOCAL() as db:
            emit_ws_event(db, user_id=user_id, event_type="model.failed", payload=payload)
            await db.commit()
    except Exception:
        logger.warning("Failed to emit model.failed", exc_info=True)


async def _finalize_generation(
    model_id: int,
    user_id: int,
    *,
    asset_url: str,
    provider: str,
    species: str,
    rig_type: str,
    style: str = "realistic",
    content_hash: str = "",
    clip_map: dict[str, str] | None = None,
) -> bool:
    """落库一次成功的生成；若已被更新的任务取代则返回 False。云端产物即终产物,后端不解析 GLB。"""
    async with SESSION_LOCAL() as db:
        model = (await db.execute(select(Companion3DModel).where(Companion3DModel.id == model_id))).scalar_one_or_none()
        if model is None:
            return False
        superseded = (
            await db.execute(
                select(Companion3DModel)
                .where(
                    Companion3DModel.user_id == user_id,
                    Companion3DModel.id > model_id,
                    Companion3DModel.status.in_(IN_FLIGHT_STATUSES),
                )
                .limit(1),
            )
        ).scalar_one_or_none() is not None
        model.asset_url = asset_url
        model.provider = provider
        model.species = species
        model.rig_type = rig_type
        model.rig_naming = "tripo"
        model.style = style
        model.status = "succeeded"
        model.has_rig = True
        model.clip_map_json = json.dumps(clip_map or {}, ensure_ascii=False)
        model.error = None

        computed_hash = content_hash
        if not computed_hash and asset_url:
            parts = asset_url.split("/", 2)
            if len(parts) == 3:
                computed_hash = get_companion_model_sha256(int(parts[1]), parts[2])
        model.content_hash = computed_hash or ""

        if not superseded:
            await db.execute(
                update(Companion3DModel)
                .where(
                    Companion3DModel.user_id == user_id,
                    Companion3DModel.active.is_(True),
                    Companion3DModel.id != model_id,
                )
                .values(active=False),
            )
            model.active = True
        await db.commit()
    return not superseded


async def _mark_generation_failed(model_id: int, reason: str) -> None:
    async with SESSION_LOCAL() as db:
        model = (await db.execute(select(Companion3DModel).where(Companion3DModel.id == model_id))).scalar_one_or_none()
        if model is not None:
            model.status = "failed"
            model.error = reason[:500]
            model.active = False
        await db.commit()


async def _mark_download_failed(model_id: int, reason: str) -> None:
    """下载或链中失败：保留 task_id 与下载地址，状态区别于终态 failed，使客户端提供重试下载而非付费重生成。"""
    async with SESSION_LOCAL() as db:
        model = (await db.execute(select(Companion3DModel).where(Companion3DModel.id == model_id))).scalar_one_or_none()
        if model is not None:
            model.status = "download_failed"
            model.error = reason[:500]
        await db.commit()


async def _persist_download_source(
    model_id: int,
    *,
    user_id: int,
    task_id: str,
    assets: tuple[Model3DAsset, ...],
    provider_label: str,
    rig_type: str,
    phase: str = "submit",
) -> None:
    """持久化当前阶段的任务恢复句柄，供重试下载与崩溃接续按阶段续跑。"""
    urls = [{"kind": a.kind, "url": a.url} for a in assets]
    async with SESSION_LOCAL() as db:
        model = (await db.execute(select(Companion3DModel).where(Companion3DModel.id == model_id))).scalar_one_or_none()
        if model is None:
            raise ModelGenerationError("companion model row vanished mid-generation")
        model.provider_task_id = task_id
        model.download_urls_json = json.dumps(urls, ensure_ascii=False)
        model.provider = provider_label
        model.rig_type = rig_type
        model.provider_phase = phase
        model.status = "pending_download"
        model.error = None
        await db.commit()
    log_paid_call(
        provider_label,
        "image_to_3d_result_persisted",
        task_id=task_id,
        user_id=user_id,
        model_id=model_id,
        urls=[u["url"] for u in urls],
    )


async def _mark_inflight_submitted(
    model_id: int,
    *,
    user_id: int,
    task_id: str,
    provider_label: str,
    rig_type: str,
    phase: str,
) -> None:
    """提交任务后立即落库 task_id，防止轮询期间服务崩溃导致已付费任务句柄丢失。"""
    async with SESSION_LOCAL() as db:
        model = (await db.execute(select(Companion3DModel).where(Companion3DModel.id == model_id))).scalar_one_or_none()
        if model is None:
            raise ModelGenerationError("companion model row vanished mid-generation")
        model.provider_task_id = task_id
        model.download_urls_json = None
        model.provider = provider_label
        model.rig_type = rig_type
        model.provider_phase = phase
        model.status = "pending_download"
        model.error = None
        await db.commit()
    log_paid_call(
        provider_label,
        "image_to_3d_inflight_persisted",
        task_id=task_id,
        user_id=user_id,
        model_id=model_id,
        phase=phase,
    )


async def _persist_clip_map(model_id: int, clip_map: dict[str, str]) -> None:
    """映射描述的是 ``provider_task_id`` 所指的那个产物，与下载恢复句柄同期固化，重试下载才能拿回它。"""
    async with SESSION_LOCAL() as db:
        await db.execute(
            update(Companion3DModel)
            .where(Companion3DModel.id == model_id)
            .values(clip_map_json=json.dumps(clip_map, ensure_ascii=False)),
        )
        await db.commit()


async def _refresh_download_urls(
    provider: ImageTo3DProvider,
    *,
    user_id: int,
    model_id: int,
    task_id: str,
) -> list[Model3DAsset]:
    """仅通过查询已持久化的供应商任务刷新过期签名地址，绝不重新提交生成。"""
    result = await provider.poll(Model3DJob(job_id=task_id))
    if result.status != "completed" or not result.assets:
        raise ModelGenerationError(f"刷新模型下载地址失败: provider 任务 {task_id} 状态 {result.status}")
    urls = [{"kind": a.kind, "url": a.url} for a in result.assets]
    async with SESSION_LOCAL() as db:
        await db.execute(
            update(Companion3DModel)
            .where(Companion3DModel.id == model_id)
            .values(download_urls_json=json.dumps(urls, ensure_ascii=False)),
        )
        await db.commit()
    log_paid_call(
        provider.provider_name,
        "image_to_3d_urls_refreshed",
        task_id=task_id,
        user_id=user_id,
        model_id=model_id,
    )
    return list(result.assets)


async def _cas_model_status(model_id: int, *, from_statuses: tuple[str, ...], to_status: str) -> bool:
    """条件状态跃迁:作为行级互斥量,防止流水线与重试下载同时认领同一模型。"""
    async with SESSION_LOCAL() as db:
        result = await db.execute(
            update(Companion3DModel)
            .where(Companion3DModel.id == model_id, Companion3DModel.status.in_(from_statuses))
            .values(status=to_status),
        )
        await db.commit()
    return bool(result.rowcount)


async def _load_model_record(model_id: int) -> Companion3DModel | None:
    async with SESSION_LOCAL() as db:
        return (await db.execute(select(Companion3DModel).where(Companion3DModel.id == model_id))).scalar_one_or_none()


async def _download_with_retry(
    provider: ImageTo3DProvider,
    *,
    user_id: int,
    model_id: int,
    task_id: str | None,
    assets: list[Model3DAsset],
    dest_dir: Path,
) -> Path:
    """有界指数退避重试:网络类错误与 5xx 重试,403 视为签名过期并刷新地址,其余 4xx 立即上抛。"""
    refreshes = 0
    last_exc: Exception | None = None
    for attempt in range(1, _DOWNLOAD_ATTEMPTS + 1):
        try:
            return await provider.download(Model3DPollResult(status="completed", assets=tuple(assets)), dest_dir)
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code == 403 and task_id and refreshes < _DOWNLOAD_URL_REFRESH_LIMIT:
                refreshes += 1
                assets = await _refresh_download_urls(provider, user_id=user_id, model_id=model_id, task_id=task_id)
                if attempt < _DOWNLOAD_ATTEMPTS:
                    continue
                raise
            if not 400 <= status_code < 500:
                last_exc = exc
            else:
                raise
        except httpx.TransportError as exc:
            last_exc = exc
        logger.warning(
            "model download attempt failed",
            extra={
                "user_id": user_id,
                "model_id": model_id,
                "task_id": task_id,
                "attempt": attempt,
                "error": str(last_exc),
            },
        )
        if attempt < _DOWNLOAD_ATTEMPTS:
            await asyncio.sleep(_DOWNLOAD_RETRY_BASE_DELAY * 2 ** (attempt - 1))
    raise last_exc  # type: ignore[misc]


async def _poll_with_progress(
    provider: ImageTo3DProvider,
    job: Model3DJob,
    user_id: int,
    stage: str,
    start_pct: int,
    end_pct: int,
) -> Model3DPollResult:
    emit_tasks: set[asyncio.Task] = set()
    deadline = time.monotonic() + SETTINGS.image_to_3d_max_poll_seconds
    started = time.monotonic()

    def _emit(our_pct: int) -> None:
        t = asyncio.create_task(_emit_progress(user_id, stage, our_pct, provider=provider.provider_name))
        emit_tasks.add(t)
        t.add_done_callback(emit_tasks.discard)

    async def _drain() -> None:
        if emit_tasks:
            await asyncio.gather(*emit_tasks)

    attempt = 0
    last_status: str | None = None
    last_progress: int = 0
    while True:
        remaining = max(0.0, deadline - time.monotonic())
        if remaining <= 0:
            await _drain()
            raise ModelGenerationError(f"3D 生成超时({SETTINGS.image_to_3d_max_poll_seconds:.0f}s)")
        result = await provider.poll(job)
        if result.status == "completed":
            _emit(end_pct)
            await _drain()
            return result
        if result.status == "failed":
            await _drain()
            raise ModelGenerationError(f"3D 生成任务失败: {result.error or stage}")
        progress_now = result.progress or 0
        # 状态转换或进度增长时重置退避；同状态无进度时递增退避间隔
        progress_grew = last_status is not None and progress_now > last_progress
        status_changed = last_status is not None and result.status != last_status
        if progress_grew or status_changed:
            attempt = 0
        elif last_status is not None:
            attempt += 1
        last_status = result.status
        last_progress = progress_now
        raw = result.progress or min(
            100,
            int(100 * (time.monotonic() - started) / SETTINGS.image_to_3d_max_poll_seconds),
        )
        _emit(start_pct + (end_pct - start_pct) * raw // 100)
        if time.monotonic() > deadline:
            await _drain()
            raise ModelGenerationError(f"3D 生成超时({SETTINGS.image_to_3d_max_poll_seconds:.0f}s)")
        sleep_for = backoff_for_poll(
            attempt,
            base_interval=SETTINGS.image_to_3d_poll_interval_seconds,
            max_interval=SETTINGS.image_to_3d_poll_backoff_max_seconds,
            remaining_seconds=remaining,
        )
        if sleep_for <= 0:
            await _drain()
            raise ModelGenerationError(f"3D 生成超时({SETTINGS.image_to_3d_max_poll_seconds:.0f}s)")
        await asyncio.sleep(sleep_for)


async def _download_one_step(
    provider: ImageTo3DProvider,
    *,
    user_id: int,
    model_id: int,
    task_id: str,
    assets: tuple[Model3DAsset, ...],
) -> bytes:
    """单跳下载 + 重试;返回 GLB bytes。"""
    if not await _cas_model_status(model_id, from_statuses=RETRYABLE_DOWNLOAD_STATUSES, to_status="downloading"):
        raise ModelGenerationError("another download attempt owns the row")
    with tempfile.TemporaryDirectory() as tmp:
        glb_path = await _download_with_retry(
            provider,
            user_id=user_id,
            model_id=model_id,
            task_id=task_id,
            assets=list(assets),
            dest_dir=Path(tmp),
        )
        return await asyncio.to_thread(glb_path.read_bytes)


async def _maybe_apply_capability(
    state: _PipelineState,
    *,
    capability_attr: str,
    progress_stage: str,
    start_pct: int,
    end_pct: int,
    user_id: int,
    model_id: int,
    provider: ImageTo3DProvider,
    rig_type: str,
    phase: str,
    submit: Callable[[], Awaitable[Model3DJob]],
) -> tuple[_PipelineState, bool]:
    """按 ``capability_attr`` ClassVar 选择是否消费该 hop;不支持或失败回退到当前状态。第二个返回值表示该跳是否真正产出了新产物。"""
    if not getattr(provider, capability_attr, False):
        return state, False
    try:
        await _emit_progress(user_id, progress_stage, start_pct, provider=provider.provider_name)
        new_job: Model3DJob = await submit()
        result = await _poll_with_progress(provider, new_job, user_id, progress_stage, start_pct, end_pct)
        await _persist_download_source(
            model_id,
            user_id=user_id,
            task_id=new_job.job_id,
            assets=result.assets,
            provider_label=provider.provider_name,
            rig_type=rig_type,
            phase=phase,
        )
        return _PipelineState(provider_task_id=new_job.job_id, assets=result.assets), True
    except Exception:
        logger.warning(
            "cloud capability %s failed; keeping current state for SPEC check",
            capability_attr,
            extra={"user_id": user_id, "model_id": model_id},
            exc_info=True,
        )
        return state, False


async def run_capability_chain(
    *,
    provider_name: str | None,
    user_id: int,
    view_filenames: dict[str, str],
    species: str,
    model_id: int,
    style: str = "realistic",
) -> None:
    """submit → poll → 可选 cloud_rig / cloud_animate_bind → download → 落库。"""
    try:
        provider = _resolve_model_provider(provider_name)
    except ModelProviderNotConfiguredError as exc:
        logger.warning(
            "model provider unconfigured",
            extra={"user_id": user_id, "model_id": model_id, "error": str(exc)},
            exc_info=True,
        )
        await _emit_model_failed(user_id, "暂未配置 3D 模型生成供应商，请联系管理员")
        await _mark_generation_failed(model_id, "provider 未配置")
        return

    record = await _load_model_record(model_id)
    if record is None:
        raise ModelGenerationError("companion model row vanished mid-generation")

    current_phase = record.provider_phase or "submit"
    current_task_id = record.provider_task_id
    rig_type = record.rig_type
    provider_label = record.provider

    try:
        if current_task_id is None:
            await _emit_progress(user_id, "uploading", 5, provider=provider.provider_name)

            def _seed(view_key: str) -> Path:
                resolved = resolve_uploaded_avatar_path(view_filenames[view_key])
                if resolved is None:
                    raise ModelGenerationError(f"{view_key} 视角种子图文件不可读: {view_filenames[view_key]}")
                return resolved[0]

            supports_multiview = bool(getattr(provider, "SUPPORTS_MULTIVIEW", False))
            multiview_paths = (
                {key: _seed(key) for key in ("front", "back") if key in view_filenames} if supports_multiview else None
            )
            await _emit_progress(user_id, "generating", 10, provider=provider.provider_name)
            job = await provider.create_image_to_model(_seed("front"), multiview_paths=multiview_paths)

            rig_type = rig_type or await select_rig_type(chat, species, user_id=user_id)
            provider_label = provider_label or _provider_result_label(
                provider.provider_name,
                multiview=multiview_paths is not None and len(multiview_paths) > 1,
            )
            await _mark_inflight_submitted(
                model_id,
                user_id=user_id,
                task_id=job.job_id,
                provider_label=provider_label,
                rig_type=rig_type,
                phase="submit",
            )
            current_phase = "submit"
            current_task_id = job.job_id

            gen_result = await _poll_with_progress(
                provider,
                Model3DJob(job_id=job.job_id),
                user_id,
                "generating",
                10,
                50,
            )
            await _persist_download_source(
                model_id,
                user_id=user_id,
                task_id=job.job_id,
                assets=gen_result.assets,
                provider_label=provider_label,
                rig_type=rig_type,
                phase="submit",
            )
            state = _PipelineState(provider_task_id=job.job_id, assets=gen_result.assets)
        elif current_phase in _PHASE_PROGRESS:
            rig_type = rig_type or await select_rig_type(chat, species, user_id=user_id)
            provider_label = provider_label or _provider_result_label(provider.provider_name)
            stage, start_pct, end_pct = _PHASE_PROGRESS[current_phase]
            job = Model3DJob(job_id=current_task_id)
            await _emit_progress(user_id, stage, start_pct, provider=provider.provider_name)
            result = await _poll_with_progress(provider, job, user_id, stage, start_pct, end_pct)
            await _persist_download_source(
                model_id,
                user_id=user_id,
                task_id=current_task_id,
                assets=result.assets,
                provider_label=provider_label,
                rig_type=rig_type,
                phase=current_phase,
            )
            state = _PipelineState(provider_task_id=current_task_id, assets=result.assets)
        else:
            raise ModelGenerationError(f"unknown provider_phase: {current_phase!r}")
    except Exception as exc:
        logger.warning(
            "3D model generation failed",
            extra={
                "user_id": user_id,
                "provider": provider.provider_name if provider else provider_name,
                "error": str(exc),
            },
            exc_info=True,
        )
        await _emit_model_failed(user_id, "3D 模型生成失败，请稍后重试")
        await _mark_generation_failed(model_id, "3D 模型生成失败，请稍后重试")
        return

    clip_map: dict[str, str] = json.loads(record.clip_map_json or "{}")
    runs_rig = current_phase == "submit" and getattr(provider, "SUPPORTS_RIGGING", False)
    rig_already_succeeded = current_phase == "rig"
    rig_just_succeeded = False
    if runs_rig:
        state, rig_just_succeeded = await _maybe_apply_capability(
            state,
            capability_attr="SUPPORTS_RIGGING",
            progress_stage="rigging",
            start_pct=90,
            end_pct=95,
            user_id=user_id,
            model_id=model_id,
            provider=provider,
            rig_type=rig_type,
            phase="rig",
            submit=lambda: provider.start_rig(state.provider_task_id, rig_type),
        )
    if (
        (rig_already_succeeded or rig_just_succeeded)
        and getattr(provider, "SUPPORTS_ANIMATE_BIND", False)
        and provider.animation_clips(rig_type)
    ):
        state, animated = await _maybe_apply_capability(
            state,
            capability_attr="SUPPORTS_ANIMATE_BIND",
            progress_stage="animate_binding",
            start_pct=92,
            end_pct=96,
            user_id=user_id,
            model_id=model_id,
            provider=provider,
            rig_type=rig_type,
            phase="animate",
            submit=lambda: provider.start_animate_bind(state.provider_task_id, rig_type),
        )
        clip_map = provider.animation_clips(rig_type) if animated else {}
        await _persist_clip_map(model_id, clip_map)

    try:
        glb_bytes = await _download_one_step(
            provider=provider,
            user_id=user_id,
            model_id=model_id,
            task_id=state.provider_task_id,
            assets=state.assets,
        )
        asset_url = await asyncio.to_thread(save_companion_model, glb_bytes, user_id=user_id)
        activated = await _finalize_generation(
            model_id,
            user_id,
            asset_url=asset_url,
            provider=provider_label,
            species=species,
            rig_type=rig_type,
            style=record.style or style,
            clip_map=clip_map,
        )

        if not activated:
            logger.info(
                "3D generation superseded by a newer run; asset saved without activating",
                extra={"user_id": user_id, "model_id": model_id},
            )
            return

        # PROTOCOL §1.3:progress 必须严格先于 model.ready,否则迟到的进度事件会让客户端在已加载模型上重现生成遮罩
        await _emit_progress(user_id, "done", 100, provider=provider.provider_name)
        await _emit_model_ready(
            user_id,
            model_id,
            asset_url,
            species=species,
            rig_type=rig_type,
            style=style,
            clip_map=clip_map,
        )
        logger.info(
            "3D model generation succeeded",
            extra={"user_id": user_id, "provider": provider.provider_name, "species": species, "rig_type": rig_type},
        )
    except Exception as exc:
        logger.warning(
            "finalize failed",
            extra={"user_id": user_id, "model_id": model_id, "error": str(exc)},
            exc_info=True,
        )
        await _mark_download_failed(model_id, "模型链接驱动失败，可重试下载")
        await _emit_model_failed(user_id, "3D 模型生成失败，可重试下载", retry_download=True, model_id=model_id)


def _launch_pipeline_task(*, model_id: int, user_id: int, **kwargs: object) -> None:
    """fire-and-forget 启动一次管道;任务异常被 try/except 捕获并标记行失败,避免事件循环静默吞任务。"""
    if model_id in _inflight_tasks and not _inflight_tasks[model_id].done():
        return

    async def _runner() -> None:
        try:
            await run_capability_chain(model_id=model_id, user_id=user_id, **kwargs)
        except Exception:
            logger.exception("pipeline task crashed; marking failed", extra={"user_id": user_id, "model_id": model_id})
            await _mark_generation_failed(model_id, "生成任务异常终止，请重试")
            await _emit_model_failed(user_id, "3D 模型生成失败，请重试", model_id=model_id)
        finally:
            _inflight_tasks.pop(model_id, None)

    task = asyncio.create_task(_runner(), name=f"companion.3d.{user_id}.{model_id}")
    _inflight_tasks[model_id] = task
    track_user_task(user_id, task, cancel_on_maintenance=False)


async def recover_stuck_model_generations() -> None:
    """启动时把 generating 行判 failed（未完成 submit）；in-flight 行留给 resume_inflight_pipelines 接续。"""
    async with SESSION_LOCAL() as db:
        await db.execute(
            update(Companion3DModel)
            .where(Companion3DModel.status == "generating")
            .values(status="failed", error="interrupted by server restart", active=False),
        )
        await db.commit()


async def resume_inflight_pipelines() -> None:
    """web 进程启动时扫描仍处于 in-flight 状态的行，交给 run_capability_chain 自驱接续。"""
    async with SESSION_LOCAL() as db:
        rows = (
            (await db.execute(select(Companion3DModel).where(Companion3DModel.status.in_(IN_FLIGHT_STATUSES))))
            .scalars()
            .all()
        )
    for row in rows:
        if not row.provider_task_id:
            await _mark_generation_failed(row.id, "服务重启时未捕获到 task_id，请重新生成")
            await _emit_model_failed(
                row.user_id,
                "3D 模型生成未完成，请重新生成",
                retry_download=False,
                model_id=row.id,
            )
            continue
        _launch_pipeline_task(
            model_id=row.id,
            user_id=row.user_id,
            provider_name=_raw_provider_name(row.provider),
            view_filenames={},
            species=row.species or "人类",
            style=row.style or "realistic",
        )


class _ResumeOutcome(Enum):
    """``_probe_paid_failure`` 的三态返回值。"""

    RESUMED = "resumed"
    CONFIRMED_FAILED = "confirmed_failed"
    UNKNOWN = "unknown"


async def _probe_paid_failure(row: Companion3DModel) -> _ResumeOutcome:
    """查询供应商真实任务状态，区分可接续、已失败与未知状态，防止重复计费。"""
    if not row.provider_task_id:
        return _ResumeOutcome.UNKNOWN
    try:
        provider = _resolve_model_provider(_raw_provider_name(row.provider))
        result = await provider.poll(Model3DJob(job_id=row.provider_task_id))
    except Exception:
        logger.warning(
            "Probe tripo task errored; row kept as failed, no re-submit",
            extra={"user_id": row.user_id, "model_id": row.id, "task_id": row.provider_task_id},
            exc_info=True,
        )
        return _ResumeOutcome.UNKNOWN
    if result.status == "completed":
        async with SESSION_LOCAL() as db:
            target = (
                await db.execute(select(Companion3DModel).where(Companion3DModel.id == row.id))
            ).scalar_one_or_none()
            if target is None:
                return _ResumeOutcome.UNKNOWN
            target.status = "pending_download"
            target.error = None
            await db.commit()
        _launch_pipeline_task(
            model_id=row.id,
            user_id=row.user_id,
            provider_name=_raw_provider_name(row.provider),
            view_filenames={},
            species=row.species or "人类",
            style=row.style or "realistic",
        )
        logger.info(
            "Resumed paid failure (tripo task was actually success)",
            extra={
                "user_id": row.user_id,
                "model_id": row.id,
                "task_id": row.provider_task_id,
                "phase": row.provider_phase,
            },
        )
        return _ResumeOutcome.RESUMED
    if result.status == "failed":
        return _ResumeOutcome.CONFIRMED_FAILED
    return _ResumeOutcome.UNKNOWN


def signed_model_url(model: Companion3DModel | None) -> str | None:
    """签发模型访问 URL；不写回行对象,否则会过期的 URL 会随下次 autoflush 落库。"""
    if model is None or not model.asset_url or not model.asset_url.startswith("companion-models/"):
        return None
    parts = model.asset_url.split("/", 2)
    if len(parts) != 3:
        return None
    return build_signed_model_url(int(parts[1]), parts[2])
