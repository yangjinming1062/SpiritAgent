"""动作制作：queries。"""

from collections.abc import Collection, Sequence

from sqlalchemy import ColumnElement, select
from sqlalchemy.ext.asyncio import AsyncSession

from features.actions.generation.state import GenerationContext
from features.actions.materials import accepted_action_asset
from features.actions.media_contracts import ActionResult
from features.actions.models import REQUIRED_SYSTEM_SLOTS, SYSTEM_SLOTS, CompanionAction, CompanionActionPack
from features.appearance.queries import active_avatar_snapshot, active_outfit_id
from features.appearance.query_contracts import AvatarSnapshot
from features.media.chain import MediaChainState
from features.media.character_images import ImageChainState

from .execution_state import _GEN_INFLIGHT
from .helpers import _artifact_abs_path


async def _active_avatar(db: AsyncSession, user_id: int) -> AvatarSnapshot | None:
    return await active_avatar_snapshot(db, user_id)


async def _active_outfit_id(db: AsyncSession, user_id: int) -> int | None:
    return await active_outfit_id(db, user_id)


def _action_order(action: str) -> tuple[int, str]:
    """任务排序键：已知动作按全集聚合顺序，可选动作排在其后并按动作名稳定排序。"""
    return (SYSTEM_SLOTS.index(action) if action in SYSTEM_SLOTS else len(SYSTEM_SLOTS), action)


def _job_result_unresolved(job: CompanionAction) -> bool:
    """当前尝试仍在制或付费提交未结算；旧候选与已采纳素材不能替代本次结果。"""
    if job.status in ("queued", "processing", "result_unknown"):
        return True
    terminal_failures = {"submit_failed", "provider_failed", "content_policy_blocked", "queue_stopped"}
    video_state = MediaChainState.model_validate_json(job.generation_state_json) if job.generation_state_json else None
    pose_state = (
        ImageChainState.model_validate_json(job.pose_generation_state_json) if job.pose_generation_state_json else None
    )
    for state in (video_state, pose_state):
        if state is None:
            continue
        if state.stop_reason == "result_unknown":
            return True
        if (
            state.phase in ("submitting", "processing")
            and state.stop_reason not in terminal_failures
            and (state.phase == "submitting" or not (state.result_url or state.source_path))
        ):
            return True
    return bool(
        job.provider_task_id
        and job.status not in ("succeeded", "review")
        and not job.artifact_path
        and (
            video_state is None
            or (
                not video_state.result_url
                and not video_state.source_path
                and video_state.stop_reason not in terminal_failures
            )
        ),
    )


def _can_resume_job(job: CompanionAction, rejected_paths: Collection[str]) -> bool:
    if job.status in ("succeeded", "review", "result_unknown") or job.stage == "invalid_asset":
        return False
    if job.media_path in rejected_paths:
        return False
    if job.generation_state_json:
        state = MediaChainState.model_validate_json(job.generation_state_json)
        if any(candidate.path in rejected_paths for candidate in state.candidates):
            return False
        if (state.phase == "submitting" and not state.queue_request_id) or state.stop_reason == "result_unknown":
            return False
        if state.phase == "ready" and state.active_index is None and not state.needs_next() and state.best() is None:
            return False
    if job.pose_generation_state_json:
        pose = ImageChainState.model_validate_json(job.pose_generation_state_json)
        if any(candidate.path in rejected_paths for candidate in pose.candidates):
            return False
        if pose.phase == "submitting" or pose.stop_reason == "result_unknown":
            return False
    return bool(
        job.provider_task_id
        or job.artifact_path
        or job.generation_state_json
        or job.pose_generation_state_json
        or job.stage == "script"
        or (job.stage == "pose" and job.pose_path),
    )


def _resumable_action_state(job: CompanionAction, rejected_paths: Collection[str]) -> MediaChainState | None:
    """只开放已有视频的下载、后处理或候选收尾，不启动新的生成尝试。"""
    if (
        job.status != "failed"
        or job.media_type != "video"
        or not job.script_json
        or not job.generation_state_json
        or not _can_resume_job(job, rejected_paths)
        or _job_result_unresolved(job)
    ):
        return None
    state = MediaChainState.model_validate_json(job.generation_state_json)
    if not state.providers or state.stop_reason == "content_policy_blocked":
        return None
    if state.phase in ("ready", "evaluating", "complete") and (state.phase != "ready" or state.active_index is None):
        candidate = (
            next((item for item in state.candidates if item.attempt == state.active_index), None)
            if state.phase == "evaluating"
            else state.best()
        )
        if candidate is not None and 0 <= candidate.attempt < len(state.providers) and candidate.result_json:
            result = ActionResult.model_validate_json(candidate.result_json)
            if all(
                _artifact_abs_path(path).is_file()
                for path in (result.clip.path, result.cover_path, result.hitmask_path)
            ):
                return state
    if state.phase != "storing" or state.active_index is None or not 0 <= state.active_index < len(state.providers):
        return None
    if state.stop_reason not in ("", "timeout", "result_download_failed", "unknown", "matting_unavailable"):
        return None
    if state.result_url or any(
        path and _artifact_abs_path(path).is_file() for path in (job.artifact_path, state.source_path)
    ):
        return state
    return None


def _must_succeed_actions(context_must: list[str]) -> set[str]:
    return set(context_must) | set(REQUIRED_SYSTEM_SLOTS)


def _can_publish_jobs(jobs: Sequence[CompanionAction], context_must: list[str]) -> bool:
    by_action = {job.key: job for job in jobs}
    for action in _must_succeed_actions(context_must):
        job = by_action.get(action)
        if job is None or accepted_action_asset(job) is None:
            return False
    return True


async def _jobs_by_pack(db: AsyncSession, *conditions: ColumnElement[bool]) -> dict[int, list[CompanionAction]]:
    rows = (await db.execute(select(CompanionAction).where(*conditions).order_by(CompanionAction.id))).scalars()
    grouped: dict[int, list[CompanionAction]] = {}
    for job in rows:
        grouped.setdefault(job.pack_id, []).append(job)
    return grouped


def _load_generation_context(pack: CompanionActionPack) -> GenerationContext | None:
    """生成包的冻结上下文；上传导入包没有上下文。"""
    return GenerationContext.model_validate_json(pack.context_json) if pack.context_json else None


def _has_pending_work(pack_id: int, jobs: Sequence[CompanionAction]) -> bool:
    """在途、排队或未确认结果的任务须保留，避免丢失句柄与后续素材引用。"""
    return pack_id in _GEN_INFLIGHT or any(_job_result_unresolved(job) for job in jobs)


async def _get_pack(db: AsyncSession, user_id: int, pack_id: int) -> CompanionActionPack | None:
    return (
        await db.execute(
            select(CompanionActionPack).where(
                CompanionActionPack.id == pack_id,
                CompanionActionPack.user_id == user_id,
            ),
        )
    ).scalar_one_or_none()
