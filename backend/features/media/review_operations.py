"""显式续审的独立预算和单飞受理；业务操作经组合根装配。"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import timedelta
from weakref import WeakValueDictionary

from infrastructure.config import SETTINGS
from infrastructure.database import SESSION_LOCAL
from infrastructure.request_errors import RequestError
from infrastructure.text import utc_now
from sqlalchemy import func, select, update

from features.jobs.service import owned_job_kind, pause_interrupted_reviews

from .continuations import available_media_actions
from .models import MediaReviewAttempt
from .review_views import MediaJobView, MediaReviewRequest

type ReviewQuery = Callable[[int, int], Awaitable[MediaJobView]]
type ReviewExecutor = Callable[[int, int, int, MediaReviewRequest], Awaitable[MediaJobView]]
_HANDLERS: dict[str, tuple[ReviewQuery, ReviewExecutor]] = {}
_ACCOUNT_LOCKS: WeakValueDictionary[int, asyncio.Semaphore] = WeakValueDictionary()
_CANDIDATE_LOCKS: WeakValueDictionary[tuple[int, int, int], asyncio.Lock] = WeakValueDictionary()


def configure_review_handler(kind: str, query: ReviewQuery, execute: ReviewExecutor) -> None:
    _HANDLERS[kind] = query, execute


async def _handler(user_id: int, job_id: int) -> tuple[ReviewQuery, ReviewExecutor]:
    async with SESSION_LOCAL() as db:
        kind = await owned_job_kind(db, user_id, job_id)
    if kind not in _HANDLERS:
        raise RequestError(status_code=404, message="找不到可检查的媒体任务")
    return _HANDLERS[kind]


async def query_media_job(user_id: int, job_id: int) -> MediaJobView:
    query, _ = await _handler(user_id, job_id)
    view = await query(user_id, job_id)
    view.continuation_actions = await available_media_actions(user_id, job_id)
    return view


async def review_media_job(user_id: int, job_id: int, body: MediaReviewRequest) -> MediaJobView:
    query, execute = await _handler(user_id, job_id)
    lock = _ACCOUNT_LOCKS.setdefault(user_id, asyncio.Semaphore(SETTINGS.media_review_concurrency_per_account))
    candidate_lock = _CANDIDATE_LOCKS.setdefault((user_id, job_id, body.slot), asyncio.Lock())
    if lock.locked() or candidate_lock.locked():
        raise RequestError(status_code=409, message="已有检查正在进行，请等待其完成")
    async with lock, candidate_lock:
        async with SESSION_LOCAL() as db:
            previous = await db.scalar(
                select(MediaReviewAttempt).where(
                    MediaReviewAttempt.user_id == user_id, MediaReviewAttempt.request_id == body.request_id
                )
            )
            if previous:
                if (previous.job_id, previous.slot, previous.basis_id) != (job_id, body.slot, body.expected_basis_id):
                    raise RequestError(status_code=409, message="请求身份已经用于另一份检查依据")
                return await query(user_id, job_id)
        view = await query(user_id, job_id)
        target = next((slot for slot in view.slots if slot.slot == body.slot), None)
        if view.revision != body.expected_revision or target is None or target.basis_id != body.expected_basis_id:
            raise RequestError(status_code=409, message="检查依据或任务状态已变化，请刷新")
        if view.state != "paused" or not target.can_review:
            raise RequestError(status_code=409, message="当前候选不需要或不能直接续审")
        async with SESSION_LOCAL() as db:
            count = await db.scalar(
                select(func.count())
                .select_from(MediaReviewAttempt)
                .where(
                    MediaReviewAttempt.user_id == user_id,
                    MediaReviewAttempt.job_id == job_id,
                    MediaReviewAttempt.slot == body.slot,
                    MediaReviewAttempt.quota_basis_id == (target.candidate_basis_id or target.basis_id),
                    MediaReviewAttempt.created_at >= utc_now() - timedelta(hours=24),
                )
            )
            if count >= SETTINGS.media_review_attempts_per_day:
                raise RequestError(status_code=429, message="这份候选的续审额度已用完，请稍后继续")
            attempt = MediaReviewAttempt(
                user_id=user_id,
                job_id=job_id,
                request_id=body.request_id,
                slot=body.slot,
                basis_id=body.expected_basis_id,
                quota_basis_id=target.candidate_basis_id or target.basis_id,
            )
            db.add(attempt)
            await db.commit()
            attempt_id = attempt.id
        result: MediaJobView | None = None
        try:
            result = await execute(user_id, job_id, body.slot, body)
        finally:
            async with SESSION_LOCAL() as db:
                await db.execute(
                    update(MediaReviewAttempt)
                    .where(MediaReviewAttempt.id == attempt_id)
                    .values(
                        state="complete" if result is not None else "interrupted",
                    )
                )
                await db.commit()
        if result is None:
            raise RuntimeError("续审未返回任务状态")
        return result


async def recover_review_attempts() -> None:
    async with SESSION_LOCAL() as db:
        interrupted = list(
            await db.scalars(select(MediaReviewAttempt.job_id).where(MediaReviewAttempt.state == "running"))
        )
        await pause_interrupted_reviews(db, interrupted)
        await db.execute(
            update(MediaReviewAttempt).where(MediaReviewAttempt.state == "running").values(state="interrupted")
        )
        await db.commit()
