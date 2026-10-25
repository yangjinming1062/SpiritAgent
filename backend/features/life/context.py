"""生活领域的统一只读上下文。

跨业务流程只通过这里取得生活状态、持续事项和近期事实，避免聊天、影像、动态
和日记分别拼出互相漂移的背景。
"""

from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import LifeEvent, LifePursuit
from .queries import empty_state_view, event_view, get_life_state_view, pursuit_view
from .schemas import LifeContext


async def read_life_context_in_session(
    db: AsyncSession,
    user_id: int,
    *,
    pursuit_limit: int = 5,
    event_limit: int = 12,
) -> LifeContext:
    """在调用方事务内读取一份一致的生活快照。"""

    state = await get_life_state_view(db, user_id) or empty_state_view(user_id)
    pursuits = await db.scalars(
        select(LifePursuit)
        .where(LifePursuit.user_id == user_id, LifePursuit.status == "active")
        .order_by(
            case((LifePursuit.id == state.active_pursuit_id, 0), else_=1),
            LifePursuit.updated_at.desc(),
            LifePursuit.id.desc(),
        )
        .limit(max(1, pursuit_limit))
    )
    events = await db.scalars(
        select(LifeEvent)
        .where(LifeEvent.user_id == user_id)
        .order_by(LifeEvent.occurred_at.desc(), LifeEvent.id.desc())
        .limit(event_limit)
    )
    return LifeContext(
        state=state,
        active_pursuits=[pursuit_view(row) for row in pursuits],
        recent_events=[event_view(row) for row in events],
    )
