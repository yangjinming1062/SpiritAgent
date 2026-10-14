"""动作制作：transactions。"""

import json

from infrastructure.assets.asset_store import save_companion_asset_async, unlink_companion_asset
from infrastructure.database import SESSION_LOCAL
from infrastructure.video_processing.ffmpeg import ActionMaterialRejectedError
from runtime.events.emit import emit_ws_event
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from features.actions.asset_retirement import action_asset_paths, retire_action_assets
from features.actions.materials import accept_action_asset, accepted_action_asset
from features.actions.media_contracts import ActionResult
from features.actions.models import CompanionAction, CompanionActionPack, removed_video_tasks
from features.actions.publishing import emit_catalog_changed, publish_action_catalog
from features.appearance.activation import activate_outfit_for_pack
from features.appearance.queries import mark_initial_video_started
from features.appearance.query_contracts import AvatarSnapshot, OutfitSnapshot
from features.media.chain import MediaChainState, media_failure_reason
from features.media.character_images import ImageChainState
from features.media.contracts import RemovedActionVideoTask
from features.media.review_service import reject_pending_action_reviews

from .errors import _generation_error, _GenerationRowGoneError
from .execution_state import logger
from .queries import _jobs_by_pack, _load_generation_context


async def _insert_pack(
    db: AsyncSession,
    user_id: int,
    *,
    avatar: AvatarSnapshot | None,
    outfit: OutfitSnapshot,
    reference_hash: str,
) -> CompanionActionPack:
    if outfit.is_initial:
        await mark_initial_video_started(db, user_id, outfit.id)
        emit_ws_event(
            db,
            user_id=user_id,
            event_type="companion.outfit.updated",
            payload={"outfit_id": outfit.id, "worn": False},
        )
    pack = CompanionActionPack(
        user_id=user_id,
        avatar_id=avatar.id if avatar is not None else None,
        outfit_id=outfit.id,
        status="processing",
        reference_hash=reference_hash,
    )
    # 版本号 = 同外观历史最大版本 + 1；版本不可覆盖，单动作请求即新版本。
    latest_version = (
        await db.execute(
            select(CompanionActionPack.pack_version)
            .where(CompanionActionPack.user_id == user_id, CompanionActionPack.outfit_id == outfit.id)
            .order_by(CompanionActionPack.pack_version.desc())
            .limit(1),
        )
    ).scalar_one_or_none()
    pack.pack_version = (latest_version or 0) + 1
    db.add(pack)
    await db.flush()
    return pack


async def _save_action_materials(
    user_id: int,
    directory: str,
    *,
    label_prefix: str,
    media_ext: str,
    media: bytes,
    cover: bytes,
    hitmask: list[int] | list[list[int]],
) -> tuple[str, str, str]:
    """按 media→cover→hitmask 顺序落盘三件套；任一步失败或取消时删除已存路径。"""
    saved: list[str] = []
    try:
        stored = await save_companion_asset_async(
            media,
            user_id=user_id,
            directory=directory,
            label=label_prefix,
            ext=media_ext,
        )
        saved.append(stored)
        cover_stored = await save_companion_asset_async(
            cover,
            user_id=user_id,
            directory=directory,
            label=f"{label_prefix}_cover",
            ext="webp",
        )
        saved.append(cover_stored)
        hitmask_stored = await save_companion_asset_async(
            json.dumps(hitmask).encode("utf-8"),
            user_id=user_id,
            directory=directory,
            label=f"{label_prefix}_hitmask",
            ext="json",
        )
    except BaseException:
        for path in saved:
            unlink_companion_asset(path)
        raise
    return stored, cover_stored, hitmask_stored


async def _emit_pack_event(user_id: int, event_type: str, payload: dict[str, object]) -> None:
    """独立短会话写入 WS 事件并提交，进度与结果在后台任务各阶段可发出。"""
    async with SESSION_LOCAL() as db:
        emit_ws_event(db, user_id=user_id, event_type=event_type, payload=payload)
        await db.commit()


async def _advance_job(job_id: int, *, stage: str, status: str = "processing", **fields: object) -> None:
    """推进生成任务行阶段；终态行不再被覆写，行已删除时中止该动作。"""
    async with SESSION_LOCAL() as db:
        job = await db.get(CompanionAction, job_id)
        if job is None:
            raise _GenerationRowGoneError
        if job.status in ("succeeded", "review", "failed"):
            return
        previous = accepted_action_asset(job)
        job.status = status
        job.stage = stage
        for key, value in fields.items():
            setattr(job, key, value)
        if status == "succeeded":
            await retire_action_assets(db, job.user_id, previous.paths() if previous else [])
            accept_action_asset(job)
        await db.commit()


async def _save_action_state(
    job: CompanionAction,
    state: MediaChainState,
    *,
    stage: str | None = None,
    status: str = "processing",
    **fields: object,
) -> None:
    """持久化素材链进度；stage 缺省时沿用任务当前阶段。"""
    job.generation_state_json = state.model_dump_json()
    if stage is not None:
        job.stage = stage
    await _advance_job(
        job.id,
        stage=job.stage,
        status=status,
        generation_state_json=job.generation_state_json,
        **fields,
    )


async def _mark_pack_failed(
    db: AsyncSession,
    pack: CompanionActionPack,
    message: str,
    *,
    stage: str | None = None,
) -> None:
    """包与未终态任务置失败并写失败事件（调用方提交）；message 为公开文案，同时作为任务与事件原因。"""
    pack.status = "failed"
    pack.error = message
    values: dict[str, object] = {"status": "failed", "error": message}
    if stage is not None:
        values["stage"] = stage
    await db.execute(
        update(CompanionAction)
        .where(
            CompanionAction.pack_id == pack.id,
            CompanionAction.status.not_in(("succeeded", "review", "failed", "result_unknown")),
        )
        .values(**values)
        .execution_options(synchronize_session=False),
    )
    emit_ws_event(
        db,
        user_id=pack.user_id,
        event_type="companion.video.failed",
        payload={"packId": pack.id, "outfitId": pack.outfit_id, "reason": message[:300]},
    )


async def _fail_pack(pack_id: int, message: str) -> None:
    """构建中的包置失败；message 为公开文案。"""
    async with SESSION_LOCAL() as db:
        pack = await db.get(CompanionActionPack, pack_id)
        if pack is None or pack.status != "processing":
            return
        await _mark_pack_failed(db, pack, message[:500])
        await db.commit()


async def _publish_catalog(db: AsyncSession, pack: CompanionActionPack) -> int | None:
    """从任务行聚合发布动作目录（spiritagent.action.pack），CAS 推进。必需槽位不齐或失败返回 None：不回滚已成功素材，可再次发布恢复。"""
    try:
        return await publish_action_catalog(db, pack)
    except Exception:  # noqa: BLE001 — 目录发布失败不回滚素材；调用方据 None 跳过目录广播与自动启用
        logger.exception("action catalog publish failed", extra={"pack_id": pack.id})
        return None


async def _record_action_failure(pack_id: int, job: CompanionAction, exc: Exception) -> None:
    """单动作失败落库，不影响同批其他动作；任务行已删除时只记录并跳过。"""
    extra = {"pack_id": pack_id, "action_id": job.id, "action": job.key}
    if isinstance(exc, _GenerationRowGoneError):
        logger.info("video action row removed during generation", extra=extra)
        return
    logger.error("video action failed", extra=extra, exc_info=exc)
    reason, _ = media_failure_reason(exc)
    status = "result_unknown" if reason == "result_unknown" else "failed"
    if isinstance(exc, ActionMaterialRejectedError):
        job.stage = "invalid_asset"
    try:
        await _advance_job(
            job.id,
            stage=job.stage,
            status=status,
            error=_generation_error(exc, media_type=job.media_type),
        )
    except _GenerationRowGoneError:
        logger.info("video action row removed before its failure was recorded", extra=extra)


async def _fail_dynamic_jobs(pack_id: int, message: str, *, action_ids: list[int] | None = None) -> None:
    async with SESSION_LOCAL() as db:
        stmt = (
            update(CompanionAction)
            .where(
                CompanionAction.pack_id == pack_id,
                CompanionAction.status == "queued",
            )
            .values(status="failed", error=message[:500])
            .execution_options(synchronize_session=False)
        )
        if action_ids is not None:
            stmt = stmt.where(CompanionAction.id.in_(action_ids))
        await db.execute(stmt)
        await db.commit()


async def _activate_locked(db: AsyncSession, pack: CompanionActionPack) -> None:
    # 每次激活（含重新穿回同一包）推进用户级外观代次；调用方持用户锁，最大值 + 1 不会并发重复。
    latest_epoch = (
        await db.execute(
            select(func.coalesce(func.max(CompanionActionPack.appearance_epoch), 0)).where(
                CompanionActionPack.user_id == pack.user_id,
            ),
        )
    ).scalar_one()
    await db.execute(
        update(CompanionActionPack)
        .where(CompanionActionPack.user_id == pack.user_id, CompanionActionPack.active.is_(True))
        .values(active=False)
        .execution_options(synchronize_session=False),
    )
    # bulk update synchronize_session=False 后必须显式标脏，重启激活同一行也能写回。
    pack.active = True
    flag_modified(pack, "active")
    pack.appearance_epoch = latest_epoch + 1
    await activate_outfit_for_pack(db, pack.user_id, pack.outfit_id)
    emit_ws_event(
        db,
        user_id=pack.user_id,
        event_type="companion.outfit.updated",
        payload={"outfit_id": pack.outfit_id, "worn": True},
    )
    emit_ws_event(
        db,
        user_id=pack.user_id,
        event_type="companion.video.activated",
        payload={"packId": pack.id, "packVersion": pack.pack_version, "outfitId": pack.outfit_id},
    )
    emit_catalog_changed(db, pack)


def _pack_assets(pack: CompanionActionPack, jobs: list[CompanionAction]) -> set[str]:
    paths = {pack.reference_path, pack.manifest_path, pack.cover_path or ""}
    context = _load_generation_context(pack)
    if context is not None:
        paths.add(context.identity_reference_path)
        paths.update(candidate.path for candidate in context.reference_chain.candidates)
        paths.add(context.reference_chain.pending_path or "")
    for job in jobs:
        paths |= action_asset_paths(job)
        if job.generation_state_json:
            state = MediaChainState.model_validate_json(job.generation_state_json)
            paths.add(state.source_path or "")
            paths.update(path for candidate in state.candidates for path in candidate.artifacts)
        if job.pose_generation_state_json:
            pose_state = ImageChainState.model_validate_json(job.pose_generation_state_json)
            paths.update(candidate.path for candidate in pose_state.candidates)
            paths.add(pose_state.pending_path or "")
        paths.update(
            (
                job.artifact_path or "",
                job.pose_path or "",
                job.media_path or "",
                job.cover_path or "",
                job.hitmask_path or "",
            ),
        )
        if job.result_json:
            result = ActionResult.model_validate_json(job.result_json)
            paths.update((result.clip.path, result.cover_path, result.hitmask_path))
    return paths - {""}


async def _remove_packs(
    db: AsyncSession,
    user_id: int,
    targets: list[CompanionActionPack],
) -> tuple[set[str], list[RemovedActionVideoTask]]:
    """删除目标包及任务行（调用方提交）；返回可回收资产路径和待撤销的远端视频句柄。共享资源（冻结参考、复用片段）只在最后一个引用消失才回收。"""
    packs = (
        (await db.execute(select(CompanionActionPack).where(CompanionActionPack.user_id == user_id))).scalars().all()
    )
    jobs_by_pack = await _jobs_by_pack(db, CompanionAction.user_id == user_id)
    target_ids = {pack.id for pack in targets}
    candidates: set[str] = set()
    for pack in targets:
        for job in jobs_by_pack.get(pack.id, []):
            await reject_pending_action_reviews(db, user_id=job.user_id, action_id=job.id, media_type=job.media_type)
        candidates |= _pack_assets(pack, jobs_by_pack.get(pack.id, []))
    for other in packs:
        if other.id not in target_ids:
            candidates -= _pack_assets(other, jobs_by_pack.get(other.id, []))
    removed = removed_video_tasks(job for pack in targets for job in jobs_by_pack.get(pack.id, []))
    for pack in targets:
        for job in jobs_by_pack.get(pack.id, []):
            await db.delete(job)
        await db.delete(pack)
    await retire_action_assets(db, user_id, candidates)
    return candidates, removed
