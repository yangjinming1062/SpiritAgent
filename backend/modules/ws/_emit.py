import json

from sqlalchemy.ext.asyncio import AsyncSession

from .models import WSEvent


def emit_ws_event(
    db: AsyncSession,
    *,
    user_id: int,
    event_type: str,
    payload: dict | str,
) -> None:
    """把一条 outbox WSEvent 行挂到当前 session;调用方负责 commit。

    ``payload`` 接受两种入参:
    - ``dict``: 自动 ``json.dumps(payload, ensure_ascii=False, default=str)``
    - ``str``: 视为已序列化的 JSON,直接写入 ``WSEvent.payload`` 列

    后者兼容极少数历史调用点(如 ``pipeline.py:707``)直接传 ``"{}"`` 字面量的现状,
    避免对这些冷僻路径做不必要的二次 JSON 包装。
    """
    rendered = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, default=str)
    db.add(WSEvent(user_id=user_id, event_type=event_type, payload=rendered))
