"""提案后台流水线：评审调度与重启恢复。对话/夜间只受理，评审在后台执行；approve 后由 generation 制作。"""

import asyncio

from components import SESSION_LOCAL, get_logger, track_user_task, utc_now
from modules.companion import ActionProposal
from sqlalchemy import and_, or_, select

from services.application.generation import kick_dynamic_action
from services.domains.actions import DEFERRED_PROPOSAL_WINDOW, get_action_accept_lock

from .design import ProposalAcceptance
from .review import defer_failed_review, review_proposal

logger = get_logger(__name__)

_REVIEW_TASKS: set[asyncio.Task[None]] = set()
# 同一提案只允许一个在途评审；重复提交创意复用 proposal_id 时不重复排队。
_INFLIGHT_REVIEWS: set[int] = set()


def schedule_accepted_proposal(acceptance: ProposalAcceptance, user_id: int) -> None:
    """提案事务提交后启动后台工作：提案进入独立评审，同 key 动作的重做或在制任务直接唤醒生成。"""
    result = acceptance.result
    if result.outcome != "pending_review":
        return
    if result.proposal_id is not None:
        schedule_proposal_review(result.proposal_id, user_id)
    elif (
        result.action_id is not None
        and acceptance.pack_id is not None
        and acceptance.existing_action in ("in_production", "redo_requested")
    ):
        kick_dynamic_action(acceptance.pack_id, result.action_id, user_id)


def schedule_proposal_review(proposal_id: int, user_id: int) -> None:
    """安排一次后台评审；不阻塞调用方事务。同一 proposal_id 去重。"""
    if proposal_id in _INFLIGHT_REVIEWS:
        return
    _INFLIGHT_REVIEWS.add(proposal_id)
    task = asyncio.create_task(_run_proposal_review(proposal_id, user_id), name=f"action.review.{proposal_id}")
    _REVIEW_TASKS.add(task)

    def _done(_task: asyncio.Task[None]) -> None:
        _REVIEW_TASKS.discard(_task)
        _INFLIGHT_REVIEWS.discard(proposal_id)

    task.add_done_callback(_done)
    track_user_task(user_id, task, cancel_on_maintenance=False)


async def _run_proposal_review(proposal_id: int, user_id: int) -> None:
    # 受理锁覆盖「读取资料 → 评审判定 → 额度占用与落库提交」，commit 后释放，避免并发评审读到相同剩余额度后全部批准；评审内部用短会话读写，模型调用期间不占连接。
    async with get_action_accept_lock(user_id):
        try:
            outcome = await review_proposal(proposal_id, user_id)
        except Exception:  # noqa: BLE001 — 评审失败必须落库为可重试状态，不静默
            logger.exception("action proposal review failed", extra={"proposal_id": proposal_id})
            await defer_failed_review(proposal_id, user_id)
            return
    if outcome is not None and outcome.decision == "approve" and outcome.action_id is not None:
        kick_dynamic_action(outcome.pack_id, outcome.action_id, user_id)


async def drain_proposal_reviews() -> None:
    tasks = list(_REVIEW_TASKS)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def resume_proposal_reviews() -> None:
    """进程重启恢复：中断的 pending 评审重新调度；deferred 只在创建期限内重试，超期不再随每次启动付费重审。"""
    cutoff = utc_now() - DEFERRED_PROPOSAL_WINDOW
    async with SESSION_LOCAL() as db:
        pending = (
            await db.execute(
                select(ActionProposal.id, ActionProposal.user_id).where(
                    or_(
                        ActionProposal.status == "pending",
                        and_(ActionProposal.status == "deferred", ActionProposal.created_at >= cutoff),
                    ),
                ),
            )
        ).all()
    for proposal_id, user_id in pending:
        schedule_proposal_review(proposal_id, user_id)
