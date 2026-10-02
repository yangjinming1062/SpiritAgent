from pydantic_core import to_json
from sqlalchemy.ext.asyncio import AsyncSession

from .models import WSEvent


def emit_ws_event(
    db: AsyncSession,
    *,
    user_id: int,
    event_type: str,
    payload: dict,
) -> None:
    """把一条 outbox WSEvent 行挂到当前 session；调用方负责 commit。载荷按 JSON 模式序列化，时间格式与 REST 一致。"""
    db.add(
        WSEvent(user_id=user_id, event_type=event_type, payload=to_json(payload, fallback=str).decode()),
    )
