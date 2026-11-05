"""聊天图片的持久生成、逐槽位检查及消息交接。"""

import asyncio
import hashlib
from dataclasses import asdict
from uuid import uuid4
from weakref import WeakValueDictionary

from infrastructure.ai.tasks import AiTaskKey
from infrastructure.assets import asset_store
from infrastructure.assets.image_references import freeze_image_references
from infrastructure.assets.references import asset_paths
from infrastructure.database import SESSION_LOCAL
from infrastructure.request_errors import RequestError
from infrastructure.text import utc_now
from runtime.events.emit import emit_ws_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.accounts.queries import user_is_active
from features.assets.lifecycle import enqueue_asset_cleanup
from features.jobs.service import bind_job_assets, bind_job_request, requested_job_id

from .chain import MediaCandidate
from .character_images import ImageChainState, generate_reviewed_images, review_image_chain, run_frozen_image_chain
from .continuations import authorize_media_waits, wait_for_media
from .delivery import deliver_image_result
from .identity_contracts import VisualIdentitySnapshot
from .image_generation import ImageGenerationError
from .manual_reviews import ManualAction, approve_candidate, manual_candidate_id, manual_candidate_views
from .models import ImageGenJob
from .reference_reader import visual_identity_is_current
from .review_contracts import ReviewPendingError, candidate_review_key, needs_review, review_basis, review_passes
from .review_evidence import review_media_intact
from .review_views import (
    ImageJobParams,
    MediaJobView,
    MediaManualRequest,
    MediaReviewRequest,
    MediaSlotView,
    review_request_matches,
)
from .turn_state import ImagePlan

_LOCKS: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()


def _qualified_candidate(state: ImageChainState, params: ImageJobParams, slot: int) -> MediaCandidate | None:
    return next(
        (
            candidate
            for candidate in state.candidates
            if candidate.slot == slot
            and candidate.evaluated
            and candidate.accepted is not False
            and review_passes(candidate.review, params.required_dimensions)
        ),
        None,
    )


async def _snapshot(row: ImageGenJob) -> MediaJobView:
    state = ImageChainState.model_validate((row.checkpoint or {}).get("chain") or row.result_json.get("chain") or {})
    params = ImageJobParams.model_validate(row.params_json)
    ids = params.media_ids
    slots: list[MediaSlotView] = []
    for index, media_id in enumerate(ids):
        candidates = [c for c in state.candidates if c.slot == index]
        chosen = _qualified_candidate(state, params, index)
        candidate = (
            chosen or next((c for c in candidates if needs_review(c.review)), None) or next(iter(candidates), None)
        )
        review = candidate.review if candidate else None
        candidate_key = candidate_review_key(
            candidate.generation_id if candidate and candidate.generation_id else state.generation_id, index
        )
        basis = review_basis(
            review,
            candidate.evidence if candidate else None,
            candidate_key if candidate or state.pending_urls else None,
        )
        status = (
            "ready"
            if chosen
            else "review_pending"
            if candidate and (review is None or review.status != "complete" or review.verdict == "review")
            else "failed"
            if candidate or row.state in {"failed", "cancelled"}
            else "pending"
        )
        delivered = media_id in row.result_json.get("delivered_slots", [])
        if row.result_json.get("delivery_blocked") and not delivered:
            status = "review_pending"
        if row.cleanup_requested_at:
            status = "failed"
        elif chosen is None and row.stage == "review_pending":
            status = "review_pending"
        manual = (row.checkpoint or {}).get("manual_generation")
        if manual and manual["slot"] == index:
            if row.state in {"queued", "running"}:
                status = "pending"
            elif row.state == "unknown":
                status = "result_unknown"
        slots.append(
            MediaSlotView(
                slot=index,
                media_id=media_id,
                status=status,
                media_revision=row.revision,
                url=asset_store.client_asset_url(chosen.path)
                if chosen
                and not row.cleanup_requested_at
                and (delivered or not row.result_json.get("delivery_blocked"))
                else None,
                preview_url=asset_store.client_asset_url(candidate.path)
                if candidate and not row.cleanup_requested_at
                else None,
                review=review,
                basis_id=basis,
                candidate_basis_id=candidate_key,
                can_review=(status == "review_pending" or bool(state.pending_urls))
                and basis is not None
                and needs_review(review)
                and not row.result_json.get("delivery_blocked"),
                error=row.error,
                candidates=await manual_candidate_views(candidates),
                can_regenerate=status != "pending"
                and row.state in {"paused", "failed"}
                and media_id not in row.result_json.get("delivered_slots", [])
                and not row.result_json.get("delivery_blocked"),
                delivered=media_id in row.result_json.get("delivered_slots", []),
            )
        )
    return MediaJobView(
        id=row.id,
        kind=row.kind,
        state=row.state,
        stage=row.stage,
        revision=row.revision,
        slots=slots,
        required_slots=len(ids),
        ready_slots=sum(slot.status == "ready" for slot in slots),
        complete=row.state == "succeeded",
        image_request=params,
        user_id=row.user_id,
        session_id=str(row.conversation_id) if row.conversation_id else None,
        source_message_id=row.source_message_id,
        bindings=row.bindings_json,
    )


async def image_job_view(user_id: int, job_id: int) -> MediaJobView:
    async with SESSION_LOCAL() as db:
        row = await db.scalar(select(ImageGenJob).where(ImageGenJob.id == job_id, ImageGenJob.user_id == user_id))
        if row is None or row.cleanup_requested_at:
            raise RequestError(status_code=404, message="找不到图片任务")
        return await _snapshot(row)


async def produce_image_batch(
    user_id: int,
    session_id: str,
    source_message_id: int | None,
    plan: ImagePlan,
    media_ids: list[str],
    *,
    directory: str,
    task: AiTaskKey,
    request_id: str,
    request_scope: str,
    goal_ids: list[str],
    parent_media_id: str | None = None,
) -> MediaJobView:
    fingerprint = hashlib.sha256(repr((asdict(plan), len(media_ids))).encode()).hexdigest()
    async with SESSION_LOCAL() as db:
        previous = await requested_job_id(db, user_id, request_id, fingerprint)
        row = await db.get(ImageGenJob, previous) if previous else None
        if row is not None:
            return await _snapshot(row)
        if previous:
            raise ValueError("原图片任务已清理，不能重复制作")
        frozen = await freeze_image_references(
            {"identity": plan.reference_image, "outfit": plan.secondary_reference_image},
            user_id=user_id,
            generation_id=hashlib.sha256(request_id.encode()).hexdigest()[:32],
            directory=directory,
        )
        params = {
            **asdict(plan),
            "reference_image": frozen.get("identity"),
            "secondary_reference_image": frozen.get("outfit"),
            "task": task.value,
            "directory": directory,
            "media_ids": media_ids,
            "goal_ids": goal_ids,
            "request_scope": request_scope,
            "parent_media_id": parent_media_id,
        }
        row = ImageGenJob(
            user_id=user_id,
            conversation_id=int(session_id),
            source_message_id=source_message_id,
            params_json=ImageJobParams.model_validate(params).model_dump(mode="json"),
            result_json={},
            bindings_json={},
            checkpoint={},
        )
        db.add(row)
        await db.flush()
        await bind_job_request(db, user_id, row.id, request_id, fingerprint)
        for index in range(len(media_ids)):
            await wait_for_media(
                db,
                user_id=user_id,
                owner_kind="chat.image",
                owner_job_id=row.id,
                dependency_job_id=row.id,
                operation_id=f"chat-image:{row.source_key}:{index}",
                automatic=True,
                context={"slot": index},
            )
        await db.commit()
        job_id = row.id
    await _run_image_job(user_id, job_id)
    return await image_job_view(user_id, job_id)


async def _block_stale_identity(db: AsyncSession, user_id: int, row: ImageGenJob) -> bool:
    """冻结身份已过期时不允许按当前身份交付，候选与检查证据保留。"""

    if all(media_id in row.result_json.get("delivered_slots", []) for media_id in row.params_json["media_ids"]):
        return False
    if not row.params_json.get("identity_json"):
        if row.params_json.get("subject") == "self":
            row.state, row.stage, row.error = (
                "paused",
                "confirmation",
                "原任务缺少冻结身份版本，候选已保留，请另建制作请求",
            )
            row.result_json = {**row.result_json, "delivery_blocked": True}
            return True
        return False
    identity = VisualIdentitySnapshot.model_validate_json(row.params_json["identity_json"])
    if await visual_identity_is_current(db, user_id, identity):
        return False
    row.state, row.stage, row.error = "paused", "confirmation", "原任务身份已失效，候选已保留，不能按当前身份交付"
    row.result_json = {**row.result_json, "delivery_blocked": True}
    return True


async def _run_image_job(
    user_id: int,
    job_id: int,
    *,
    review_only: bool = False,
    slot: int | None = None,
    expected: MediaReviewRequest | None = None,
) -> None:
    async with _LOCKS.setdefault(job_id, asyncio.Lock()):
        async with SESSION_LOCAL() as db:
            row = await db.scalar(
                select(ImageGenJob).where(ImageGenJob.id == job_id, ImageGenJob.user_id == user_id).with_for_update()
            )
            if row is None or row.cleanup_requested_at or row.state in {"cancelled", "succeeded"}:
                return
            if expected is not None and not review_request_matches(await _snapshot(row), expected):
                raise RequestError(status_code=409, message="检查依据或任务版本已变化")
            input_params = ImageJobParams.model_validate(row.params_json)
            params = input_params.model_dump(mode="json")
            chain = ImageChainState.model_validate((row.checkpoint or {}).get("chain", {}))
            row.state, row.stage = "running", "reviewing" if review_only else "image"
            await db.commit()

        async def save(value: ImageChainState) -> None:
            async with SESSION_LOCAL() as db:
                current = await db.get(ImageGenJob, job_id, with_for_update=True)
                if current is None or current.cleanup_requested_at or current.state == "cancelled":
                    raise asyncio.CancelledError("discarded")
                current.checkpoint = {"chain": value.model_dump(mode="json")}
                await bind_job_assets(db, user_id, job_id, asset_paths(current.checkpoint, user_id), "generation")
                await db.commit()

        error: str | None = None
        try:
            if review_only:
                await review_image_chain(user_id, chain, task=AiTaskKey(params["task"]), save_progress=save, slot=slot)
            else:
                await generate_reviewed_images(
                    params["prompt"],
                    user_id=user_id,
                    storage_directory=params["directory"],
                    reference_image=params["reference_image"] or "",
                    identity_reference=params["reference_image"] or "",
                    secondary_reference_image=params["secondary_reference_image"],
                    identity_reference_mode=params["identity_reference_mode"],
                    identity_required=input_params.identity_required,
                    size=params["size"],
                    n=len(params["media_ids"]),
                    state=chain,
                    save_progress=save,
                    task=AiTaskKey(params["task"]),
                    goal_review="required",
                )
        except (ImageGenerationError, ReviewPendingError) as exc:
            error = str(exc)
        except asyncio.CancelledError:
            error = "制作已中断，原候选和提交状态已保留"
            raise
        finally:
            async with SESSION_LOCAL() as db:
                current = await db.get(ImageGenJob, job_id, with_for_update=True)
                if current and current.state != "cancelled" and not current.cleanup_requested_at:
                    current.checkpoint = {"chain": chain.model_dump(mode="json")}
                    current.result_json = {**current.result_json, "chain": chain.model_dump(mode="json")}
                    pending = (
                        bool(chain.pending_urls)
                        or any(c.review and c.review.status == "blocked" for c in chain.candidates)
                        or any(
                            _qualified_candidate(chain, input_params, index) is None
                            and any(c.slot == index and (needs_review(c.review)) for c in chain.candidates)
                            for index in range(len(params["media_ids"]))
                        )
                    )
                    qualified = sum(
                        _qualified_candidate(chain, input_params, index) is not None
                        for index in range(len(params["media_ids"]))
                    )
                    current.state, current.stage = (
                        ("paused", "review_pending")
                        if pending
                        else ("paused", "confirmation")
                        if review_only
                        else ("paused", "confirmation")
                        if qualified == len(params["media_ids"])
                        else ("paused", "review_pending")
                        if chain.candidates
                        else ("failed", "failed")
                    )
                    if chain.phase == "submitting" and not chain.candidates:
                        current.state, current.stage = "unknown", "result_unknown"
                    current.error = error
                    await _block_stale_identity(db, user_id, current)
                    await db.flush()
                    if not review_only:
                        await deliver_image_result(db, await _snapshot(current))
                    emit_ws_event(
                        db, user_id=user_id, event_type="media.review.updated", payload={"job_id": str(job_id)}
                    )
                    await db.commit()


async def review_image_job(user_id: int, job_id: int, slot: int, expected: MediaReviewRequest) -> MediaJobView:
    await _run_image_job(user_id, job_id, review_only=True, slot=slot, expected=expected)
    return await image_job_view(user_id, job_id)


async def manually_review_image(
    db: AsyncSession, user_id: int, job_id: int, request: MediaManualRequest
) -> ManualAction:
    row = await db.get(ImageGenJob, job_id)
    params = ImageJobParams.model_validate(row.params_json)
    if (
        row.cleanup_requested_at
        or request.slot >= len(params.media_ids)
        or await _block_stale_identity(db, user_id, row)
    ):
        raise RequestError(status_code=409, message="原任务已失效，候选已保留")
    await authorize_media_waits(db, user_id, job_id, "chat.image")
    chain = ImageChainState.model_validate((row.checkpoint or {}).get("chain") or row.result_json.get("chain") or {})
    if params.media_ids[request.slot] in row.result_json.get("delivered_slots", []):
        raise RequestError(status_code=409, message="这个槽位已经交付，不能覆盖已交付版本")
    if request.action == "regenerate":
        fresh = ImageChainState(
            inputs=chain.inputs.model_copy(update={"n": 1}, deep=True) if chain.inputs and chain.providers else None,
            providers=chain.providers if chain.inputs else [],
            storage_directory=chain.storage_directory or params.directory,
            generation_id=uuid4().hex,
        )
        for candidate in chain.candidates:
            if candidate.slot == request.slot:
                candidate.excluded = True
        row.checkpoint = {
            **(row.checkpoint or {}),
            "chain": chain.model_dump(mode="json"),
            "manual_generation": {
                "slot": request.slot,
                "request_id": request.request_id,
                "chain": fresh.model_dump(mode="json"),
            },
        }
        row.state, row.stage, row.error = "queued", "image", None
        await db.flush()
        await deliver_image_result(db, await _snapshot(row))
        return ManualAction(restart_job_id=job_id, audit_context={"params_json": row.params_json})
    selected = next(
        (
            candidate
            for candidate in chain.candidates
            if candidate.slot == request.slot and manual_candidate_id(candidate) == request.candidate_id
        ),
        None,
    )
    if selected is None:
        raise RequestError(status_code=409, message="所选候选已变化，请刷新")
    required = params.required_dimensions
    await approve_candidate(user_id, selected, required, request, db=db)
    for candidate in chain.candidates:
        if candidate.slot == request.slot and candidate is not selected:
            candidate.excluded = True
    row.checkpoint = {"chain": chain.model_dump(mode="json")}
    row.result_json = {**row.result_json, "chain": chain.model_dump(mode="json")}
    row.state, row.stage, row.error = "paused", "confirmation", None
    await bind_job_assets(db, user_id, job_id, asset_paths(row.checkpoint, user_id), "review")
    return ManualAction(audit_context={"params_json": row.params_json})


def _fold_manual_generation(row: ImageGenJob) -> None:
    checkpoint = dict(row.checkpoint or {})
    manual = checkpoint.get("manual_generation")
    if not manual:
        return
    fresh = ImageChainState.model_validate(manual["chain"])
    original = ImageChainState.model_validate(checkpoint.get("chain") or row.result_json.get("chain") or {})
    if original.inputs is None and fresh.inputs is not None:
        original.inputs = fresh.inputs.model_copy(update={"n": len(row.params_json["media_ids"])}, deep=True)
    if not original.providers:
        original.providers = fresh.providers
    slot = manual["slot"]
    original.candidates = [
        candidate
        for candidate in original.candidates
        if candidate.slot != slot or candidate.generation_id != fresh.generation_id
    ] + [
        candidate.model_copy(update={"slot": slot, "generation_id": fresh.generation_id})
        for candidate in fresh.candidates
    ]
    retained = [
        (url, pending_slot)
        for url, pending_slot in zip(original.pending_urls, original.pending_slots, strict=True)
        if pending_slot != slot
    ]
    original.pending_urls = [url for url, _ in retained] + fresh.pending_urls
    original.pending_slots = [pending_slot for _, pending_slot in retained] + [slot] * len(fresh.pending_urls)
    original.phase, original.stop_reason = fresh.phase, fresh.stop_reason
    checkpoint["chain"] = original.model_dump(mode="json")
    row.checkpoint = checkpoint
    row.result_json = {**row.result_json, "chain": original.model_dump(mode="json")}


async def run_manual_image_job(user_id: int, job_id: int) -> None:
    async with _LOCKS.setdefault(job_id, asyncio.Lock()):
        async with SESSION_LOCAL() as db:
            row = await db.get(ImageGenJob, job_id, with_for_update=True)
            if row is None or row.user_id != user_id or row.state != "queued" or row.cleanup_requested_at:
                return
            manual = (row.checkpoint or {}).get("manual_generation")
            if not manual:
                return
            params = ImageJobParams.model_validate(row.params_json)
            fresh = ImageChainState.model_validate(manual["chain"])
            row.state, row.stage = "running", "image"
            await db.commit()

        async def save(value: ImageChainState) -> None:
            async with SESSION_LOCAL() as db:
                current = await db.get(ImageGenJob, job_id, with_for_update=True)
                if current is None or current.state != "running" or current.cleanup_requested_at:
                    raise asyncio.CancelledError()
                current.checkpoint = {
                    **current.checkpoint,
                    "manual_generation": {**manual, "chain": value.model_dump(mode="json")},
                }
                await bind_job_assets(db, user_id, job_id, asset_paths(current.checkpoint, user_id), "generation")
                await db.commit()

        error = None
        try:
            if fresh.inputs is not None:
                await run_frozen_image_chain(user_id, fresh, task=params.task, save_progress=save)
            else:
                await generate_reviewed_images(
                    params.prompt,
                    user_id=user_id,
                    task=params.task,
                    storage_directory=params.directory,
                    reference_image=params.reference_image or "",
                    identity_reference=params.reference_image or "",
                    secondary_reference_image=params.secondary_reference_image,
                    identity_reference_mode=params.identity_reference_mode,
                    identity_required=params.identity_required,
                    size=params.size,
                    n=1,
                    state=fresh,
                    save_progress=save,
                )
        except (ImageGenerationError, ReviewPendingError) as exc:
            error = str(exc)
        finally:
            async with SESSION_LOCAL() as db:
                current = await db.get(ImageGenJob, job_id, with_for_update=True)
                if current and current.state == "running" and not current.cleanup_requested_at:
                    current.checkpoint = {
                        **current.checkpoint,
                        "manual_generation": {**manual, "chain": fresh.model_dump(mode="json")},
                    }
                    _fold_manual_generation(current)
                    combined = ImageChainState.model_validate(current.checkpoint["chain"])
                    qualified = sum(
                        _qualified_candidate(combined, params, index) is not None
                        for index in range(len(params.media_ids))
                    )
                    pending = bool(combined.pending_urls) or bool(combined.candidates)
                    current.state, current.stage = (
                        ("unknown", "result_unknown")
                        if fresh.phase == "submitting"
                        else ("paused", "confirmation")
                        if qualified == len(params.media_ids)
                        else ("paused", "review_pending")
                        if pending
                        else ("failed", "failed")
                    )
                    current.error = error
                    await _block_stale_identity(db, user_id, current)
                    await db.flush()
                    await deliver_image_result(db, await _snapshot(current))
                    emit_ws_event(
                        db, user_id=user_id, event_type="media.review.updated", payload={"job_id": str(job_id)}
                    )
                    await db.commit()


async def bind_image_reply(
    db: AsyncSession, user_id: int, session_id: str, job_id: int, media_id: str, message_id: int
) -> MediaJobView:
    row = await db.scalar(
        select(ImageGenJob)
        .where(ImageGenJob.id == job_id, ImageGenJob.user_id == user_id, ImageGenJob.conversation_id == int(session_id))
        .with_for_update()
    )
    if row is None or row.cleanup_requested_at or media_id not in row.params_json["media_ids"]:
        raise ValueError("图片任务不属于当前消息")
    await _block_stale_identity(db, user_id, row)
    existing = row.bindings_json.get(media_id)
    if existing and existing != str(message_id):
        raise ValueError("图片已绑定其他消息")
    row.bindings_json = {**row.bindings_json, media_id: str(message_id)}
    chain = ImageChainState.model_validate((row.checkpoint or {}).get("chain") or row.result_json.get("chain") or {})
    selected = _qualified_candidate(
        chain, ImageJobParams.model_validate(row.params_json), row.params_json["media_ids"].index(media_id)
    )
    if selected is not None:
        if not await asyncio.to_thread(review_media_intact, selected.review.evidence, selected.path):
            row.result_json = {**row.result_json, "delivery_blocked": True}
            row.state, row.stage, row.error = "paused", "confirmation", "候选或检查证据已变化，结果已保留"
        elif not row.result_json.get("delivery_blocked"):
            row.result_json = {
                **row.result_json,
                "delivered_slots": list(dict.fromkeys([*row.result_json.get("delivered_slots", []), media_id])),
            }
            if all(value in row.result_json["delivered_slots"] for value in row.params_json["media_ids"]):
                row.state, row.stage = "succeeded", "complete"
    await db.flush()
    emit_ws_event(db, user_id=user_id, event_type="media.review.updated", payload={"job_id": str(job_id)})
    return await _snapshot(row)


async def record_image_delivery(db: AsyncSession, user_id: int, job_id: int, media_id: str) -> int:
    row = await db.scalar(
        select(ImageGenJob).where(ImageGenJob.id == job_id, ImageGenJob.user_id == user_id).with_for_update()
    )
    if row is None or row.cleanup_requested_at or media_id not in row.bindings_json:
        raise ValueError("原消息的媒体绑定已失效")
    row.result_json = {
        **row.result_json,
        "delivered_slots": list(dict.fromkeys([*row.result_json.get("delivered_slots", []), media_id])),
    }
    if all(value in row.result_json["delivered_slots"] for value in row.params_json["media_ids"]):
        row.state, row.stage = "succeeded", "complete"
    await db.flush()
    return row.revision


async def checked_image_result(db: AsyncSession, user_id: int, job_id: int) -> MediaJobView | None:
    if not await user_is_active(db, user_id):
        return None
    row = await db.scalar(
        select(ImageGenJob).where(ImageGenJob.id == job_id, ImageGenJob.user_id == user_id).with_for_update()
    )
    if row is None or row.cleanup_requested_at or row.state == "cancelled":
        return None
    if await _block_stale_identity(db, user_id, row):
        return None
    chain = ImageChainState.model_validate((row.checkpoint or {}).get("chain") or row.result_json.get("chain") or {})
    for index in range(len(row.params_json["media_ids"])):
        chosen = _qualified_candidate(chain, ImageJobParams.model_validate(row.params_json), index)
        if chosen and not await asyncio.to_thread(review_media_intact, chosen.review.evidence, chosen.path):
            row.state, row.stage, row.error = "paused", "review_pending", "原候选或检查证据异常，结果已保留"
            row.result_json = {**row.result_json, "delivery_blocked": True}
            return None
    return await _snapshot(row)


async def conversation_image_jobs(db: AsyncSession, user_id: int, session_id: str) -> tuple[MediaJobView, ...]:
    rows = await db.scalars(
        select(ImageGenJob).where(
            ImageGenJob.user_id == user_id,
            ImageGenJob.conversation_id == int(session_id),
            ImageGenJob.cleanup_requested_at.is_(None),
        )
    )
    return tuple([await _snapshot(row) for row in rows])


async def invalidate_image_jobs(db: AsyncSession, user_id: int, message_ids: set[int], session_ids: set[str]) -> None:
    rows = await db.scalars(select(ImageGenJob).where(ImageGenJob.user_id == user_id).with_for_update())
    for row in rows:
        if (
            row.source_message_id in message_ids
            or str(row.conversation_id) in session_ids
            or any(int(value) in message_ids for value in row.bindings_json.values())
        ):
            row.cleanup_requested_at = utc_now()
            row.state, row.stage = "cancelled", "discarded"


async def transfer_image_bindings(db: AsyncSession, user_id: int, from_message_id: int, to_message_id: int) -> None:
    rows = await db.scalars(select(ImageGenJob).where(ImageGenJob.user_id == user_id).with_for_update())
    for row in rows:
        if str(from_message_id) in row.bindings_json.values():
            row.bindings_json = {
                key: str(to_message_id) if value == str(from_message_id) else value
                for key, value in row.bindings_json.items()
            }


async def recover_image_jobs() -> None:
    async with SESSION_LOCAL() as db:
        rows = await db.scalars(select(ImageGenJob).where(ImageGenJob.state.in_(("queued", "running"))))
        for row in rows:
            if (
                row.state == "queued"
                and (row.checkpoint or {}).get("manual_generation", {}).get("chain", {}).get("phase") == "ready"
            ):
                continue
            _fold_manual_generation(row)
            unknown = (row.checkpoint or {}).get("chain", {}).get("phase") == "submitting"
            row.state, row.stage = ("unknown", "result_unknown") if unknown else ("paused", "review_pending")
            row.error = "图片制作已中断，请核对原任务"
        await db.commit()


async def cleanup_user_image_jobs(user_id: int) -> None:
    async with SESSION_LOCAL() as db:
        rows = list(
            await db.scalars(
                select(ImageGenJob)
                .where(ImageGenJob.user_id == user_id, ImageGenJob.cleanup_requested_at.is_not(None))
                .with_for_update()
            )
        )
        for row in rows:
            await enqueue_asset_cleanup(
                db, user_id, asset_paths([row.params_json, row.checkpoint, row.result_json], user_id)
            )
            await db.delete(row)
        await db.commit()
