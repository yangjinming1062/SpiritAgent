"""生活领域的统一只读上下文。

跨业务流程只通过这里取得生活状态、持续事项和近期事实，避免聊天、影像、动态
和日记分别拼出互相漂移的背景。
"""

from sqlalchemy.ext.asyncio import AsyncSession

from .queries import empty_state_view, get_life_state_view, list_events, list_pursuits
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
    pursuits = await list_pursuits(db, user_id, status="active", limit=pursuit_limit)
    events = await list_events(db, user_id, limit=event_limit)
    return LifeContext(
        state=state,
        active_pursuits=pursuits.items,
        recent_events=events.items,
    )
