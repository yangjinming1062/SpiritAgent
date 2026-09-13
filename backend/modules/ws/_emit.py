import json

from sqlalchemy.ext.asyncio import AsyncSession

from .models import WSEvent


def emit_ws_event(
    db: AsyncSession,
    *,
    user_id: int,
    event_type: str,
    payload: dict,
) -> None:
    """把一条 outbox WSEvent 行挂到当前 session；调用方负责 commit。"""
    db.add(
        WSEvent(user_id=user_id, event_type=event_type, payload=json.dumps(payload, ensure_ascii=False, default=str)),
    )
