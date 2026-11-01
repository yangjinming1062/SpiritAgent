"""动作评审任务的恢复与收敛；启动和恢复不重新提交付费评审。"""

from infrastructure.database import SESSION_LOCAL
from runtime.lifecycle import RuntimeParticipant
from runtime.task_supervisor import TASK_SUPERVISOR
from sqlalchemy import select

from .models import ActionDefinition
from .service import ensure_builtin_actions, recover_interrupted_reviews


async def resume_action_reviews(user_id: int | None = None) -> None:
    async with SESSION_LOCAL() as db:
        await recover_interrupted_reviews(db, user_id)
        owners = select(ActionDefinition.user_id).where(ActionDefinition.source == "preset").distinct()
        if user_id is not None:
            owners = owners.where(ActionDefinition.user_id == user_id)
        user_ids = list(await db.scalars(owners))
        await db.commit()
    for owner_id in user_ids:
        async with SESSION_LOCAL() as db:
            await ensure_builtin_actions(db, owner_id)
            await db.commit()


async def drain_action_reviews() -> None:
    await TASK_SUPERVISOR.drain(owner="action_library.review")
    async with SESSION_LOCAL() as db:
        await recover_interrupted_reviews(db)
        await db.commit()


def runtime_participants() -> tuple[RuntimeParticipant, ...]:
    return (
        RuntimeParticipant(
            "action_library.review",
            recover=resume_action_reviews,
            resume_user=resume_action_reviews,
            drain=drain_action_reviews,
        ),
    )
