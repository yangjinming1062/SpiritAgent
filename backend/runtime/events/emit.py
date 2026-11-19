from infrastructure.library_visibility import current_library_work
from pydantic_core import to_jsonable_python
from sqlalchemy.ext.asyncio import AsyncSession

from runtime.events.models import WSEvent


def emit_ws_event(db: AsyncSession, *, user_id: int, event_type: str, payload: dict) -> None:
    """把一条 outbox WSEvent 行挂到当前 session；调用方负责 commit。载荷按 JSON 模式序列化，时间格式与 REST 一致。"""
    work_id = current_library_work()
    if work_id is not None and event_type not in {
        "media.review.updated",
        "outfit.creation.continue",
        "visual.preparation.continue",
    }:
        return
    db.add(
        WSEvent(
            user_id=user_id,
            event_type=event_type,
            library_work_id=work_id,
            payload=to_jsonable_python(payload, fallback=str),
        )
    )
