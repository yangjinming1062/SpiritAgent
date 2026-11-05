"""人工选择与重做的独立操作身份；模型判断和业务授权分别保留。"""

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Literal
from uuid import uuid4

from infrastructure.assets.asset_store import (
    build_data_uri,
    client_asset_url,
    read_valid_image_data_uri,
    resolve_asset_reference,
)
from infrastructure.assets.references import asset_paths
from infrastructure.database import SESSION_LOCAL
from infrastructure.request_errors import RequestError
from infrastructure.text import utc_now
from infrastructure.video_processing.desktop import sample_desktop_video_frames
from runtime.background import TaskScope
from runtime.events.emit import emit_ws_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.accounts.queries import user_is_active
from features.assets.service import ensure_asset_references_in_session
from features.jobs.service import bind_job_assets, owned_job_control

from .chain import MediaCandidate
from .character_images import ImageChainState, ReviewedImageInput
from .models import MediaManualOperation
from .review_contracts import HumanReviewApproval, VisualReview, review_context
from .review_evidence import asset_file_hash, freeze_review_evidence, review_media_intact
from .review_views import MediaCandidateView, MediaJobView, MediaManualRequest


@dataclass(frozen=True)
class ManualAction:
    restart_job_id: int | None = None
    audit_context: dict = field(default_factory=dict)
    result_snapshot: dict = field(default_factory=dict)


type ManualHandler = Callable[[AsyncSession, int, int, MediaManualRequest], Awaitable[ManualAction]]
type ManualQuery = Callable[[int, int], Awaitable[MediaJobView]]
_HANDLERS: dict[str, tuple[ManualQuery, ManualHandler]] = {}
_RUNNERS: dict[str, Callable[[int, int], Awaitable[object]]] = {}
_OPERATIONS = TaskScope("media.manual_operations")


def configure_manual_handler(
    kind: str, query: ManualQuery, handler: ManualHandler, runner: Callable[[int, int], Awaitable[object]] | None = None
) -> None:
    _HANDLERS[kind] = query, handler
    if runner is not None:
        _RUNNERS[kind] = runner


def manual_candidate_id(candidate: MediaCandidate) -> str:
    return hashlib.sha256(
        review_context(
            {
                "path": candidate.path,
                "sha256": asset_file_hash(candidate.path),
                "generation_id": candidate.generation_id,
                "attempt": candidate.attempt,
                "slot": candidate.slot,
            }
        ).encode()
    ).hexdigest()


async def manual_candidate_views(
    candidates: list[MediaCandidate], *, media_type: Literal["image", "video"] = "image"
) -> list[MediaCandidateView]:
    def build() -> list[MediaCandidateView]:
        return [
            MediaCandidateView(
                id=manual_candidate_id(candidate),
                preview_url=client_asset_url(candidate.path),
                media_type=media_type,
                review=candidate.review,
                can_approve=asset_file_hash(candidate.path) is not None,
            )
            for candidate in candidates
        ]

    return await asyncio.to_thread(build)


async def approve_candidate(
    user_id: int, candidate: MediaCandidate, required: tuple[str, ...], request: MediaManualRequest, *, db: AsyncSession
) -> None:
    if await asyncio.to_thread(manual_candidate_id, candidate) != request.candidate_id:
        raise RequestError(status_code=409, message="候选内容已变化，请刷新后重新确认")
    expected_hash = await asyncio.to_thread(asset_file_hash, candidate.path)
    references = await ensure_asset_references_in_session(db, user_id, {candidate.path})
    if references[candidate.path].content_hash != expected_hash:
        raise RequestError(status_code=409, message="候选文件与原资产记录不符，请重新生成")
    resolved = resolve_asset_reference(candidate.path)
    if resolved is None:
        raise RequestError(status_code=409, message="候选文件已不可用，请重新生成")
    previous = candidate.review
    if resolved[1].startswith("video/"):
        samples = await asyncio.to_thread(sample_desktop_video_frames, resolved[0], sample_count=9)
        images = tuple(build_data_uri(frame, "image/webp") for frame in samples.frames)
        context = {"frame_times_seconds": samples.times_seconds}
    else:
        image = await asyncio.to_thread(read_valid_image_data_uri, candidate.path)
        if not image:
            raise RequestError(status_code=409, message="候选图片无法读取，请重新生成")
        images, context = (image,), {}
    context["previous_model_basis"] = previous.evidence.basis_id if previous and previous.evidence else None
    evidence = await freeze_review_evidence(
        user_id,
        images,
        prompt="用户查看原候选后明确采用；确定性文件与业务权限独立校验",
        context=context,
        media_path=candidate.path,
    )
    if evidence.media_hash != expected_hash or not await asyncio.to_thread(
        review_media_intact, evidence, candidate.path
    ):
        raise RequestError(status_code=409, message="候选在确认期间发生变化，请刷新")
    report = VisualReview(
        required=required, evidence=evidence, reference_basis=previous.reference_basis if previous else None
    )
    report.human_approval = HumanReviewApproval(
        request_id=request.request_id,
        approved_at=utc_now(),
        content_basis_id=evidence.content_basis_id,
        dimensions=required,
    )
    candidate.review, candidate.evaluated, candidate.accepted = report, True, True


async def approve_image_chain(
    user_id: int,
    chain: ImageChainState,
    request: MediaManualRequest,
    *,
    db: AsyncSession,
    required: tuple[str, ...] | None = None,
) -> MediaCandidate:
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
    required = required or (
        ("identity", "goal", "quality")
        if isinstance(chain.inputs, ReviewedImageInput) and chain.inputs.purpose == "character"
        else ("goal", "quality")
    )
    await approve_candidate(user_id, selected, required, request, db=db)
    for candidate in chain.candidates:
        if candidate.slot == request.slot and candidate is not selected:
            candidate.accepted = False
    chain.candidates = [selected, *(candidate for candidate in chain.candidates if candidate is not selected)]
    chain.stop_reason = "accepted"
    chain.phase = "complete"
    return selected


def regenerate_image_chain(original: ImageChainState) -> ImageChainState:
    if original.inputs is None or not original.providers:
        raise RequestError(status_code=409, message="原任务缺少制作输入，无法重新生成")
    return ImageChainState(
        inputs=original.inputs.model_copy(deep=True),
        providers=original.providers,
        storage_directory=original.storage_directory,
        generation_id=uuid4().hex,
    )


async def protect_manual_assets(db: AsyncSession, user_id: int, job_id: int, value: object) -> None:
    paths = {
        path
        for path in asset_paths(value, user_id)
        if (resolved := resolve_asset_reference(path)) is not None and resolved[0].is_file()
    }
    await bind_job_assets(db, user_id, job_id, paths, "review")


async def manual_media_action(user_id: int, job_id: int, request: MediaManualRequest) -> MediaJobView:
    operation_id = None
    result_job_id = job_id
    async with SESSION_LOCAL() as db:
        job = await owned_job_control(db, user_id, job_id, lock=True)
        if job is None or job.kind not in _HANDLERS:
            raise RequestError(status_code=404, message="找不到可人工处理的媒体任务")
        query, handle = _HANDLERS[job.kind]
        previous = await db.scalar(
            select(MediaManualOperation).where(
                MediaManualOperation.user_id == user_id, MediaManualOperation.request_id == request.request_id
            )
        )
        if previous is not None:
            if (previous.job_id, previous.slot, previous.action, previous.candidate_id) != (
                job_id,
                request.slot,
                request.action,
                request.candidate_id,
            ):
                raise RequestError(status_code=409, message="请求身份已用于另一项操作")
            operation_id = previous.id
            result_job_id = previous.result_json.get("job_id", job_id)
        else:
            if (
                job.revision != request.expected_revision
                or job.state not in {"paused", "failed"}
                or job.cancel_requested
                or job.cleanup_pending
                or not await user_is_active(db, user_id)
            ):
                raise RequestError(status_code=409, message="媒体状态或操作权限已变化，请刷新")
            if request.action == "approve" and request.candidate_id is None:
                raise RequestError(status_code=400, message="请选择要采用的候选")
            operation = MediaManualOperation(
                user_id=user_id,
                job_id=job_id,
                request_id=request.request_id,
                slot=request.slot,
                action=request.action,
                candidate_id=request.candidate_id,
                snapshot_json={
                    "checkpoint": deepcopy(job.checkpoint),
                    "state": job.state,
                    "stage": job.stage,
                    "job_revision": job.revision,
                },
            )
            db.add(operation)
            action = await handle(db, user_id, job_id, request)
            operation.snapshot_json = {**operation.snapshot_json, **deepcopy(action.audit_context)}
            await db.flush()
            current = await owned_job_control(db, user_id, job_id)
            await protect_manual_assets(db, user_id, job_id, [current.checkpoint, action.result_snapshot])
            result_job_id = action.restart_job_id or job_id
            operation.result_json = {
                "accepted": True,
                "job_id": result_job_id,
                "start_pending": action.restart_job_id is not None,
                "checkpoint": current.checkpoint,
                **action.result_snapshot,
            }
            emit_ws_event(db, user_id=user_id, event_type="media.review.updated", payload={"job_id": str(job_id)})
            await db.commit()
            operation_id = operation.id
    if operation_id is not None:
        await run_manual_operation(user_id, operation_id)
    return await query(user_id, result_job_id)


async def run_manual_operation(user_id: int, operation_id: int) -> None:
    async with SESSION_LOCAL() as db:
        operation = await db.get(MediaManualOperation, operation_id)
        if operation is None or operation.user_id != user_id or not operation.result_json.get("start_pending"):
            return
        job = await owned_job_control(db, user_id, int(operation.result_json["job_id"]))
        runner = _RUNNERS.get(job.kind) if job else None
        run_job_id = job.id if job is not None and job.state == "queued" and runner is not None else None
    if run_job_id is not None:
        started = await runner(user_id, run_job_id)
        if started is False:
            return
    async with SESSION_LOCAL() as db:
        operation = await db.get(MediaManualOperation, operation_id, with_for_update=True)
        if operation is not None:
            operation.result_json = {**operation.result_json, "start_pending": False}
            await db.commit()


async def recover_manual_operations(user_id: int | None = None) -> None:
    async with SESSION_LOCAL() as db:
        statement = select(MediaManualOperation.user_id, MediaManualOperation.id).where(
            MediaManualOperation.result_json["start_pending"].as_boolean().is_(True)
        )
        if user_id is not None:
            statement = statement.where(MediaManualOperation.user_id == user_id)
        pending = list((await db.execute(statement.order_by(MediaManualOperation.id).limit(100))).all())
    for owner, operation_id in pending:
        _OPERATIONS.spawn(
            operation_id,
            lambda: run_manual_operation(owner, operation_id),
            name=f"media.manual_operation.{operation_id}",
            user_id=owner,
            cancel_on_maintenance=False,
        )


async def drain_manual_operations() -> None:
    await _OPERATIONS.drain()
