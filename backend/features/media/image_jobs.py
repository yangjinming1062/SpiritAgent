"""聊天图片的持久生成、逐槽位检查及消息交接。"""

import asyncio
import hashlib
from dataclasses import asdict

from infrastructure.ai.tasks import AiTaskKey
from infrastructure.assets import asset_store
from infrastructure.assets.image_references import freeze_image_references
from infrastructure.assets.references import asset_paths
from infrastructure.async_io import KeyedLocks
from infrastructure.database import SESSION_LOCAL
from infrastructure.domain_locks import lock_domain
from infrastructure.logger import get_logger
from infrastructure.request_errors import RequestError
from infrastructure.text import utc_now
from runtime.background import TaskScope
from runtime.events.emit import emit_ws_event
from runtime.paid_work import GenerationWorkPaused
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from features.accounts.queries import user_is_active
from features.assets.lifecycle import enqueue_asset_cleanup
from features.jobs.service import bind_job_assets, bind_job_request, requested_job_id

from .character_images import ImageChainState, generate_reviewed_images
from .continuations import wait_for_media
from .delivery import deliver_image_result
from .identity_contracts import VisualIdentitySnapshot
from .image_generation import ImageGenerationError, ImageResultDownloadError, resolve_image_gen_chain
from .models import ImageGenJob
from .prompts import chat_image_edit_prompt, personal_performance_goal
from .reference_reader import visual_identity_is_current
from .review_contracts import MediaSelection, ReviewPendingError
from .review_evidence import review_media_intact
from .review_views import ImageJobParams, MediaJobView, MediaSlotView
from .turn_state import ImagePlan

_LOCKS = KeyedLocks[int]()
logger = get_logger(__name__)


class _ImageDeliveryEnded(Exception):
    """身份在制作期间失效，已有槽位结算已提交。"""


def _selected_results(state: ImageChainState, params: ImageJobParams) -> ImageChainState:
    selected = [
        candidate
        for slot in range(len(params.media_ids))
        if (candidate := state.delivery_candidate(slot, params.required_dimensions))
    ]
    return ImageChainState(generation_id=state.generation_id, candidates=selected, selections=state.selections)


async def _snapshot(row: ImageGenJob) -> MediaJobView:
    state = ImageChainState.model_validate((row.checkpoint or {}).get("chain") or row.result_json.get("chain") or {})
    params = ImageJobParams.model_validate(row.params_json)
    slots: list[MediaSlotView] = []
    for index, media_id in enumerate(params.media_ids):
        delivered = media_id in row.result_json.get("delivered_slots", [])
        chosen = state.delivery_candidate(index, params.required_dimensions)
        if (
            row.cleanup_requested_at
            or media_id in row.result_json.get("invalid_slots", [])
            or (not delivered and row.result_json.get("delivery_blocked"))
        ):
            chosen = None
        if chosen:
            status = "ready"
        elif row.state == "unknown":
            status = "result_unknown"
        elif (
            row.state in {"succeeded", "failed", "cancelled"}
            or row.cleanup_requested_at
            or media_id in row.result_json.get("invalid_slots", [])
            or (index in state.selections and state.selections[index].mode == "not_delivered")
        ):
            status = "failed"
        else:
            status = "pending"
        slots.append(
            MediaSlotView(
                slot=index,
                media_id=media_id,
                status=status,
                media_revision=row.revision,
                url=asset_store.client_asset_url(chosen.path) if chosen else None,
                review=chosen.review if chosen else None,
                delivered=delivered,
                error="本次图片未完成" if status == "failed" else None,
            )
        )
    return MediaJobView(
        id=row.id,
        kind=row.kind,
        state=row.state,
        stage=row.stage,
        revision=row.revision,
        slots=slots,
        required_slots=len(params.media_ids),
        ready_slots=sum(slot.status == "ready" for slot in slots),
        complete=row.state == "succeeded" and row.stage == "complete",
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
    parent_image: str | None = None,
) -> MediaJobView:
    frozen_plan = asdict(plan)
    if plan.pose_goal is None:
        frozen_plan.pop("pose_goal")
    if not plan.performance_context:
        frozen_plan.pop("performance_context")
    fingerprint = hashlib.sha256(repr((frozen_plan, len(media_ids))).encode()).hexdigest()
    async with SESSION_LOCAL() as db:
        await lock_domain(db, user_id, f"media.image.request:{request_id}")
        previous = await requested_job_id(db, user_id, request_id, fingerprint)
        row = await db.get(ImageGenJob, previous) if previous else None
        if row is not None:
            return await _snapshot(row)
        if previous:
            raise ValueError("原图片任务已清理，不能重复制作")
        references = {"identity": plan.reference_image, "outfit": plan.secondary_reference_image}
        if parent_image:
            edits = await resolve_image_gen_chain(
                db,
                user_id,
                task=task,
                has_reference=True,
                image_edit=True,
                prompt_chars=len(chat_image_edit_prompt(plan.goal, plan.performance_context, plan.pose_goal)),
            )
            if edits.providers:
                references["edit"] = parent_image
        frozen = await freeze_image_references(
            references,
            user_id=user_id,
            generation_id=hashlib.sha256(request_id.encode()).hexdigest()[:32],
            directory=directory,
        )
        params = {
            **frozen_plan,
            "reference_image": frozen.get("identity"),
            "secondary_reference_image": frozen.get("outfit"),
            "task": task.value,
            "directory": directory,
            "media_ids": media_ids,
            "goal_ids": goal_ids,
            "request_scope": request_scope,
            "parent_media_id": parent_media_id,
            "edit_reference": frozen.get("edit"),
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
        await bind_job_assets(db, user_id, row.id, set(frozen.values()), "input")
        await bind_job_request(db, user_id, row.id, request_id, fingerprint)
        await db.commit()
        job_id = row.id
    await _run_image_job(user_id, job_id)
    return await image_job_view(user_id, job_id)


async def _block_stale_identity(db: AsyncSession, user_id: int, row: ImageGenJob) -> bool:
    """冻结身份已过期时不允许按当前身份交付，候选与检查证据保留。"""

    if row.result_json.get("delivery_blocked"):
        return True
    delivered = set(row.result_json.get("delivered_slots", []))
    media_ids = row.params_json["media_ids"]
    if delivered.issuperset(media_ids):
        return False
    if frozen := row.params_json.get("identity_json"):
        if await visual_identity_is_current(db, user_id, VisualIdentitySnapshot.model_validate_json(frozen)):
            return False
        reason = "原任务身份已失效"
    elif row.params_json.get("subject") == "self":
        reason = "原任务缺少冻结身份版本"
    else:
        return False
    chain = ImageChainState.model_validate((row.checkpoint or {}).get("chain") or row.result_json.get("chain") or {})
    for slot, media_id in enumerate(media_ids):
        if media_id not in delivered:
            chain.selections[slot] = MediaSelection(mode="not_delivered", reason=reason)
    if row.checkpoint is not None:
        row.checkpoint = {"chain": chain.model_dump(mode="json")}
    params = ImageJobParams.model_validate(row.params_json)
    retained = _selected_results(chain, params)
    row.state, row.stage, row.error = "failed", "failed", reason
    row.result_json = {
        **row.result_json,
        "chain": retained.model_dump(mode="json", exclude_defaults=True),
        "delivery_blocked": True,
    }
    return True


async def _run_image_job(user_id: int, job_id: int) -> None:
    async with _LOCKS[job_id]:
        async with SESSION_LOCAL() as db:
            row = await db.scalar(
                select(ImageGenJob).where(ImageGenJob.id == job_id, ImageGenJob.user_id == user_id).with_for_update()
            )
            if row is None or row.cleanup_requested_at or row.state not in {"queued", "running"}:
                return
            if await _block_stale_identity(db, user_id, row):
                await db.flush()
                await deliver_image_result(db, await _snapshot(row))
                emit_ws_event(db, user_id=user_id, event_type="media.review.updated", payload={"job_id": str(job_id)})
                await db.commit()
                return
            input_params = ImageJobParams.model_validate(row.params_json)
            params = input_params.model_dump(mode="json")
            chain = ImageChainState.model_validate((row.checkpoint or {}).get("chain", {}))
            row.state, row.stage = "running", "image"
            await db.commit()

        async def save(value: ImageChainState) -> None:
            async with SESSION_LOCAL() as db:
                current = await db.get(ImageGenJob, job_id, with_for_update=True)
                if current is None or current.cleanup_requested_at or current.state == "cancelled":
                    raise asyncio.CancelledError("discarded")
                if current.result_json.get("delivery_blocked"):
                    raise _ImageDeliveryEnded
                previous_choices = ((current.checkpoint or {}).get("chain") or {}).get("selections", {})
                new_choices = bool(set(map(str, value.selections)) - previous_choices.keys())
                current.checkpoint = {"chain": value.model_dump(mode="json")}
                await bind_job_assets(
                    db, user_id, job_id, asset_paths(current.checkpoint, user_id) - {value.pending_path}, "generation"
                )
                if new_choices and current.bindings_json:
                    await _block_stale_identity(db, user_id, current)
                    await db.flush()
                    await deliver_image_result(db, await _snapshot(current))
                await db.commit()
                if current.result_json.get("delivery_blocked"):
                    raise _ImageDeliveryEnded

        error: str | None = None
        try:
            await generate_reviewed_images(
                chat_image_edit_prompt(input_params.goal, input_params.performance_context, input_params.pose_goal)
                if input_params.edit_reference
                else input_params.prompt,
                user_id=user_id,
                storage_directory=params["directory"],
                reference_image=input_params.edit_reference or input_params.reference_image or "",
                identity_reference=params["reference_image"] or "",
                secondary_reference_image=None
                if input_params.edit_reference
                else input_params.secondary_reference_image,
                identity_secondary_reference=input_params.secondary_reference_image,
                image_edit=bool(input_params.edit_reference),
                identity_reference_mode=params["identity_reference_mode"],
                identity_required=input_params.identity_required,
                size=params["size"],
                n=len(params["media_ids"]),
                state=chain,
                save_progress=save,
                task=AiTaskKey(params["task"]),
                goal_review="required",
                delivery_goal=input_params.goal,
                pose_goal=input_params.pose_goal
                or personal_performance_goal(input_params.goal, input_params.performance_context),
            )
        except _ImageDeliveryEnded:
            return
        except ImageResultDownloadError:
            # 原地址和槽位均已登记，后台只接收原结果。
            chain.resume_download()
            await save(chain)
            await _queue_image_receive(user_id, job_id)
            return
        except (ImageGenerationError, ReviewPendingError) as exc:
            error = str(exc)
        except (GenerationWorkPaused, SQLAlchemyError):
            raise
        except Exception:
            logger.exception("Image job stopped before delivery", extra={"user_id": user_id, "job_id": job_id})
            error = "本次图片制作未完成"
        async with SESSION_LOCAL() as db:
            current = await db.get(ImageGenJob, job_id, with_for_update=True)
            if (
                not current
                or current.state == "cancelled"
                or current.cleanup_requested_at
                or current.result_json.get("delivery_blocked")
            ):
                return
            current.checkpoint = {"chain": chain.model_dump(mode="json")}
            retained = _selected_results(chain, input_params)
            current.result_json = {
                **current.result_json,
                "chain": retained.model_dump(mode="json", exclude_defaults=True),
            }
            if chain.unknown_submission():
                current.state, current.stage = "unknown", "result_unknown"
            elif retained.candidates:
                current.state, current.stage = (
                    "succeeded",
                    "complete" if len(retained.candidates) == len(input_params.media_ids) else "partial",
                )
            else:
                current.state, current.stage = "failed", "failed"
            current.error = error
            await _block_stale_identity(db, user_id, current)
            await db.flush()
            await deliver_image_result(db, await _snapshot(current))
            emit_ws_event(db, user_id=user_id, event_type="media.review.updated", payload={"job_id": str(job_id)})
            await db.commit()


async def _queue_image_receive(user_id: int, job_id: int) -> None:
    async with SESSION_LOCAL() as db:
        row = await db.get(ImageGenJob, job_id, with_for_update=True)
        if row and not row.cleanup_requested_at and row.state == "running":
            row.state, row.stage = "queued", "receiving"
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
    params = ImageJobParams.model_validate(row.params_json)
    selected = chain.delivery_candidate(params.media_ids.index(media_id), params.required_dimensions)
    if selected is not None:
        if not await asyncio.to_thread(review_media_intact, selected.review.evidence, selected.path):
            row.result_json = {
                **row.result_json,
                "invalid_slots": list(dict.fromkeys([*row.result_json.get("invalid_slots", []), media_id])),
            }
        elif not row.result_json.get("delivery_blocked"):
            row.result_json = {
                **row.result_json,
                "delivered_slots": list(dict.fromkeys([*row.result_json.get("delivered_slots", []), media_id])),
            }
    await db.flush()
    view = await _snapshot(row)
    index = params.media_ids.index(media_id)
    if view.slots[index].status in {"pending", "result_unknown"}:
        # 消息选定版本后才登记续行；同目标未选用的版本只保留制作记录。
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
    return view


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
    await _block_stale_identity(db, user_id, row)
    chain = ImageChainState.model_validate((row.checkpoint or {}).get("chain") or row.result_json.get("chain") or {})
    params = ImageJobParams.model_validate(row.params_json)
    for index, media_id in enumerate(params.media_ids):
        chosen = chain.delivery_candidate(index, params.required_dimensions)
        if (
            chosen
            and media_id not in row.result_json.get("invalid_slots", [])
            and not await asyncio.to_thread(review_media_intact, chosen.review.evidence, chosen.path)
        ):
            row.result_json = {
                **row.result_json,
                "invalid_slots": [*row.result_json.get("invalid_slots", []), media_id],
            }
    return await _snapshot(row)


async def conversation_image_jobs(db: AsyncSession, user_id: int, session_id: str) -> tuple[MediaJobView, ...]:
    rows = await db.scalars(
        select(ImageGenJob)
        .where(
            ImageGenJob.user_id == user_id,
            ImageGenJob.conversation_id == int(session_id),
            ImageGenJob.cleanup_requested_at.is_(None),
        )
        .order_by(ImageGenJob.id)
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


_RECOVERY = TaskScope("media.image_recovery")


async def recover_image_jobs(user_id: int | None = None) -> None:
    async with SESSION_LOCAL() as db:
        query = select(ImageGenJob.id, ImageGenJob.user_id).where(
            ImageGenJob.state.in_(("queued", "running")), ImageGenJob.cleanup_requested_at.is_(None)
        )
        if user_id is not None:
            query = query.where(ImageGenJob.user_id == user_id)
        jobs = list(await db.execute(query))
    for job_id, owner in jobs:
        _RECOVERY.spawn(
            job_id,
            lambda owner=owner, job_id=job_id: _run_image_job(owner, job_id),
            name=f"image.recover.{job_id}",
            user_id=owner,
        )


async def drain_image_jobs() -> None:
    await _RECOVERY.drain()


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
