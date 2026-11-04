"""自主造型制作受理和恢复，复用衣柜的生成与采纳流程。"""

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import timedelta

from infrastructure.assets.asset_store import compute_file_sha256, resolve_asset_reference
from infrastructure.database import SESSION_LOCAL
from infrastructure.text import ensure_utc, utc_now
from runtime.events.emit import emit_ws_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.accounts.queries import user_is_active
from features.companion.visual_state import persona_snapshot
from features.jobs.service import bind_job_assets, bind_job_request, requested_job_id
from features.life.schemas import LifeStepRequest
from features.life.use_cases import (
    bind_step_in_session,
    require_step_submission,
    update_business_steps_in_session,
    wait_business_media_in_session,
)
from features.media.character_images import ImageChainState
from features.media.continuations import finish_media_operation, wait_for_media
from features.media.identity_contracts import VisualIdentitySnapshot
from features.media.image_generation import ImageResultDownloadError

from .identity import require_visual_identity, visual_identity_is_current
from .models import AvatarAsset, CompanionOutfit, OutfitCreationJob
from .outfit_service import OutfitContinuationBlocked, confirm_outfit, create_outfit_draft, get_outfit_policy

_LOCKS: dict[int, asyncio.Lock] = {}


@dataclass(frozen=True)
class OutfitCreation:
    id: int
    state: str
    outfit_id: int | None
    error: str | None


async def create_outfit_job(user_id: int, description: str, request: LifeStepRequest) -> OutfitCreation:
    async with _LOCKS.setdefault(user_id, asyncio.Lock()), SESSION_LOCAL() as db:
        fingerprint = hashlib.sha256(description.encode()).hexdigest()
        job_id = await requested_job_id(db, user_id, request.idempotency_key, fingerprint)
        row = await db.get(OutfitCreationJob, job_id) if job_id is not None else None
        if row is None:
            if job_id is not None:
                raise ValueError("原造型制作已清理，不能重新提交同一请求")
            await require_step_submission(db, user_id, request)
            if await get_outfit_policy(db, user_id) == "locked":
                raise ValueError("自主添置造型已关闭")
            identity = await require_visual_identity(db, user_id)
            avatar = await db.get(AvatarAsset, identity.avatar_id)
            if avatar is None:
                raise ValueError("确认形象已失效")
            row = OutfitCreationJob(
                user_id=user_id,
                description=description,
                checkpoint={
                    "identity": identity.model_dump(mode="json"),
                    "identity_revision": identity.revision,
                    "identity_path": avatar.seed_fullbody_url,
                },
            )
            db.add(row)
            await db.flush()
            await bind_job_assets(db, user_id, row.id, {avatar.seed_fullbody_url}, "input")
            await bind_job_request(db, user_id, row.id, request.idempotency_key, fingerprint)
            await bind_step_in_session(db, user_id, request, business_ref={"job_id": row.id})
            await db.commit()
            await db.refresh(row)
        return OutfitCreation(row.id, row.state, row.result_outfit_id, row.error)


async def run_outfit_job(user_id: int, job_id: int) -> OutfitCreation:
    async with _LOCKS.setdefault(user_id, asyncio.Lock()):
        async with SESSION_LOCAL() as db:
            row = await db.scalar(
                select(OutfitCreationJob).where(OutfitCreationJob.user_id == user_id, OutfitCreationJob.id == job_id)
            )
            if row is None:
                raise ValueError("造型制作不存在")
            if row.stage == "manual_image":
                return OutfitCreation(row.id, row.state, row.result_outfit_id, row.error)
            if row.state == "unknown" and (row.checkpoint or {}).get("failure_code") == "result_download_failed":
                if row.not_before is not None and ensure_utc(row.not_before) > utc_now():
                    return OutfitCreation(row.id, row.state, row.result_outfit_id, row.error)
                row.state, row.error, row.not_before = "running", None, None
                image_chain = ImageChainState.model_validate(row.checkpoint["image_chain"])
                image_chain.resume_download()
                row.checkpoint = {**row.checkpoint, "image_chain": image_chain.model_dump(mode="json")}
                await update_business_steps_in_session(
                    db, user_id, kind="outfit.create", business_ref={"job_id": job_id}, state="running"
                )
            if row.state == "unknown" and row.result_outfit_id is not None:
                outfit = await db.scalar(
                    select(CompanionOutfit).where(
                        CompanionOutfit.user_id == user_id, CompanionOutfit.id == row.result_outfit_id
                    )
                )
                chain = ImageChainState.model_validate((row.checkpoint or {}).get("image_chain", {}))
                candidate = chain.best(0) if chain.phase == "complete" else None
                original = resolve_asset_reference(candidate.path) if candidate is not None else None
                adopted = (
                    resolve_asset_reference(outfit.fullbody_url)
                    if outfit is not None and outfit.status == "ready"
                    else None
                )
                if (
                    original
                    and adopted
                    and await asyncio.to_thread(compute_file_sha256, original[0])
                    == await asyncio.to_thread(compute_file_sha256, adopted[0])
                ):
                    row.state, row.stage, row.error = "succeeded", "complete", None
                    await finish_media_operation(
                        db, user_id, f"outfit:{row.source_key}:continue", {"outfit_id": str(row.result_outfit_id)}
                    )
                    emit_ws_event(
                        db, user_id=user_id, event_type="media.review.updated", payload={"job_id": str(job_id)}
                    )
                    await update_business_steps_in_session(
                        db,
                        user_id,
                        kind="outfit.create",
                        business_ref={"job_id": job_id},
                        state="completed",
                        result_ref={"job_id": job_id, "outfit_id": row.result_outfit_id},
                    )
                    await db.commit()
            if row.state not in {"queued", "running"}:
                return OutfitCreation(row.id, row.state, row.result_outfit_id, row.error)
            frozen = (row.checkpoint or {}).get("identity")
            persona = await persona_snapshot(db, user_id)
            if (
                not await user_is_active(db, user_id)
                or persona is None
                or not persona.is_complete
                or await get_outfit_policy(db, user_id) == "locked"
                or frozen is None
                or not await visual_identity_is_current(db, user_id, VisualIdentitySnapshot.model_validate(frozen))
            ):
                return await _hold_outfit_permission(db, user_id, row, "原授权或身份已变化，候选与制作进度已保留")
            row.state = "running"
            description = row.description
            await db.commit()
        try:
            async with SESSION_LOCAL() as db:
                outfit = await create_outfit_draft(db, user_id, description=description, job_id=job_id)
                if outfit.status == "draft":
                    outfit = await confirm_outfit(db, user_id, outfit.id, creation_job_id=job_id)
                if outfit.status != "ready":
                    raise ValueError("原造型未就绪，不能记录完成")
                row = await db.get(OutfitCreationJob, job_id)
                if row is None:
                    raise ValueError("造型制作已失效")
                row.result_outfit_id, row.state, row.stage, row.error = outfit.id, "succeeded", "complete", None
                await finish_media_operation(
                    db, user_id, f"outfit:{row.source_key}:continue", {"outfit_id": str(outfit.id)}
                )
                emit_ws_event(db, user_id=user_id, event_type="media.review.updated", payload={"job_id": str(job_id)})
                await update_business_steps_in_session(
                    db,
                    user_id,
                    kind="outfit.create",
                    business_ref={"job_id": job_id},
                    state="completed",
                    result_ref={"job_id": job_id, "outfit_id": outfit.id},
                )
                await db.commit()
                return OutfitCreation(job_id, "succeeded", outfit.id, None)
        except OutfitContinuationBlocked as exc:
            async with SESSION_LOCAL() as db:
                current = await db.get(OutfitCreationJob, job_id, with_for_update=True)
                if current is not None and current.state not in {"cancelled", "succeeded"}:
                    return await _hold_outfit_permission(db, user_id, current, str(exc))
                return OutfitCreation(
                    job_id,
                    current.state if current else "cancelled",
                    current.result_outfit_id if current else None,
                    str(exc),
                )
        except BaseException as exc:
            pending_review = False
            async with SESSION_LOCAL() as db:
                row = await db.get(OutfitCreationJob, job_id)
                if row is not None and row.state == "running":
                    chain = (row.checkpoint or {}).get("image_chain", {})
                    download_failed = isinstance(exc, ImageResultDownloadError) or isinstance(
                        exc.__cause__, ImageResultDownloadError
                    )
                    unknown = download_failed or (
                        isinstance(exc, asyncio.CancelledError)
                        or chain.get("phase") == "submitting"
                        or chain.get("stop_reason") == "result_unknown"
                    )
                    pending_review = (
                        chain.get("stop_reason")
                        in {
                            "review_pending",
                            "goal_review_unavailable",
                            "goal_review_invalid",
                            "goal_review_blocked",
                        }
                        or bool(chain.get("candidates"))
                        and not unknown
                    )
                    row.state, row.error = (
                        ("paused" if pending_review else "unknown" if unknown else "failed"),
                        "候选已保留，等待检查" if pending_review else "制作中断，须核对原结果" if unknown else str(exc),
                    )
                    if pending_review:
                        row.stage = "review_pending"
                        await wait_for_media(
                            db,
                            user_id=user_id,
                            owner_kind="outfit.create",
                            owner_job_id=job_id,
                            dependency_job_id=job_id,
                            operation_id=f"outfit:{row.source_key}:continue",
                            automatic=row.request_source == "autonomous",
                            context={"source": row.request_source},
                        )
                    if download_failed:
                        row.checkpoint = {**(row.checkpoint or {}), "failure_code": "result_download_failed"}
                        row.not_before = utc_now() + timedelta(minutes=15)
                        row.error = str(exc)
                    if pending_review:
                        if row.request_source == "autonomous":
                            await wait_business_media_in_session(db, user_id, "outfit.create", job_id, row.error)
                    else:
                        await update_business_steps_in_session(
                            db,
                            user_id,
                            kind="outfit.create",
                            business_ref={"job_id": job_id},
                            state=row.state,
                            error=row.error,
                            failure_code="result_download_failed"
                            if download_failed
                            else "result_unknown"
                            if unknown
                            else "execution_failed",
                        )
                    await db.commit()
            if pending_review:
                return OutfitCreation(job_id, "paused", row.result_outfit_id, row.error)
            raise


async def outfit_creation_continue_requested(user_id: int, payload: dict) -> None:
    await run_outfit_job(user_id, int(payload["job_id"]))


async def _hold_outfit_permission(
    db: AsyncSession, user_id: int, row: OutfitCreationJob, reason: str
) -> OutfitCreation:
    row.state, row.stage, row.error = "paused", "confirmation", reason
    row.checkpoint = {**(row.checkpoint or {}), "permission_wait": True}
    await wait_for_media(
        db,
        user_id=user_id,
        owner_kind="outfit.create",
        owner_job_id=row.id,
        dependency_job_id=row.id,
        operation_id=f"outfit:{row.source_key}:continue",
        automatic=row.request_source == "autonomous",
        context={"source": row.request_source},
    )
    await wait_business_media_in_session(db, user_id, "outfit.create", row.id, reason)
    await db.commit()
    return OutfitCreation(row.id, row.state, row.result_outfit_id, row.error)
