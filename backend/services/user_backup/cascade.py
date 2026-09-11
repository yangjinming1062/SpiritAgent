from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from .serializers import TABLE_MODELS, TABLES


async def clear_user_scoped_rows(db: AsyncSession, user_id: int, tables: list[str]) -> None:
    # 未包含会话的备份不得清除目标会话；messages 随 conversations 级联删除。
    for table in reversed(TABLES):
        if table not in tables or table in {"user_preferences", "messages"}:
            continue
        model = TABLE_MODELS[table]
        await db.execute(delete(model).where(model.user_id == user_id))
