"""提案后台流水线：评审调度与重启恢复。

对话/夜间只受理提案；评审在后台执行。approve 后由 generation 编排制作。
"""

import asyncio

from components import SESSION_LOCAL, get_logger, track_user_task
from modules.companion import ActionProposal
from sqlalchemy import select

from services.application.generation.video.service import kick_dynamic_action
from services.domains.actions import get_action_accept_lock

from .review import review_proposal

logger = get_logger(__name__)


def schedule_action_generation(pack_id: int, action_id: int, user_id: int) -> None:
    """制作失败重做：直接启动生成（无评审）。"""
    kick_dynamic_action(pack_id, action_id, user_id)


_REVIEW_TASKS: set[asyncio.Task[None]] = set()
# 同一提案只允许一个在途评审；重复提交创意复用 proposal_id 时不重复排队。
_INFLIGHT_REVIEWS: set[int] = set()


def schedule_proposal_review(proposal_id: int, user_id: int, *, reference_image: str = "") -> None:
    """安排一次后台评审；不阻塞调用方事务。同一 proposal_id 去重。"""
    if proposal_id in _INFLIGHT_REVIEWS:
        return
    _INFLIGHT_REVIEWS.add(proposal_id)
    task = asyncio.create_task(
        _run_proposal_review(proposal_id, user_id, reference_image=reference_image),
        name=f"action.review.{proposal_id}",
    )
    _REVIEW_TASKS.add(task)

    def _done(_task: asyncio.Task[None]) -> None:
        _REVIEW_TASKS.discard(_task)
        _INFLIGHT_REVIEWS.discard(proposal_id)

    task.add_done_callback(_done)
    track_user_task(user_id, task, cancel_on_maintenance=False)


async def _run_proposal_review(proposal_id: int, user_id: int, *, reference_image: str = "") -> None:
    # 受理锁覆盖「评审判定 → 制作额度占用 → 落库提交」，锁在 commit 后释放，
    # 避免并发评审读到相同剩余额度后全部批准。
    decision = "defer"
    action_id: int | None = None
    proposal_pack = 0
    async with get_action_accept_lock(user_id), SESSION_LOCAL() as db:
        proposal = await db.get(ActionProposal, proposal_id)
        if proposal is None or proposal.user_id != user_id or proposal.status not in ("pending", "deferred"):
            return
        try:
            decision = await review_proposal(db, user_id, proposal, reference_image=reference_image)
        except Exception:  # noqa: BLE001 — 评审失败必须落库为可重试状态，不静默
            logger.exception("action proposal review failed", extra={"proposal_id": proposal_id})
            proposal.status = "deferred"
            proposal.review_reason = "评审执行失败，可重试"
            decision = "defer"
        await db.commit()
        proposal_pack = proposal.pack_id
        action_id = proposal.action_id
    if decision == "approve" and action_id is not None:
        kick_dynamic_action(proposal_pack, action_id, user_id)


async def drain_proposal_reviews() -> None:
    tasks = list(_REVIEW_TASKS)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def resume_proposal_reviews() -> None:
    """进程重启恢复：pending/deferred 评审重新调度。"""
    async with SESSION_LOCAL() as db:
        pending = (
            await db.execute(
                select(ActionProposal.id, ActionProposal.user_id).where(
                    ActionProposal.status.in_(("pending", "deferred")),
                ),
            )
        ).all()
    for proposal_id, user_id in pending:
        schedule_proposal_review(proposal_id, user_id)
