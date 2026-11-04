"""业务所有者的持久等待；通知可重复，继续操作始终复用原身份。"""

from collections.abc import Awaitable, Callable
from typing import Literal

from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from infrastructure.text import utc_now
from pydantic import BaseModel, ConfigDict
from runtime.events.emit import emit_ws_event
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from features.jobs.service import owned_job_control

from .models import MediaContinuation


class MediaWait(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)
    id: int
    user_id: int
    operation_id: str
    owner_kind: str
    owner_job_id: int
    dependency_job_id: int
    automatic: bool
    context_json: dict


type ContinuationHandler = Callable[[AsyncSession, MediaWait], Awaitable[dict | None]]
_HANDLERS: dict[str, ContinuationHandler] = {}
logger = get_logger(__name__)


async def finish_media_operation(db: AsyncSession, user_id: int, operation_id: str, result: dict) -> None:
    row = await db.scalar(
        select(MediaContinuation)
        .where(MediaContinuation.user_id == user_id, MediaContinuation.operation_id == operation_id)
        .with_for_update()
    )
    if row is not None:
        row.state, row.result_json = "complete", result


def configure_continuation_handler(kind: str, handler: ContinuationHandler) -> None:
    _HANDLERS[kind] = handler


async def authorize_media_waits(db: AsyncSession, user_id: int, owner_job_id: int, owner_kind: str) -> None:
    await db.execute(
        update(MediaContinuation)
        .where(
            MediaContinuation.user_id == user_id,
            MediaContinuation.owner_job_id == owner_job_id,
            MediaContinuation.owner_kind == owner_kind,
            MediaContinuation.state == "waiting",
        )
        .values(automatic=True)
    )


async def wait_for_media(
    db: AsyncSession,
    *,
    user_id: int,
    owner_kind: str,
    owner_job_id: int,
    dependency_job_id: int,
    operation_id: str,
    automatic: bool,
    context: dict,
) -> None:
    row = await db.scalar(
        select(MediaContinuation)
        .where(MediaContinuation.user_id == user_id, MediaContinuation.operation_id == operation_id)
        .with_for_update()
    )
    if row is None:
        db.add(
            MediaContinuation(
                user_id=user_id,
                operation_id=operation_id,
                owner_kind=owner_kind,
                owner_job_id=owner_job_id,
                dependency_job_id=dependency_job_id,
                automatic=automatic,
                context_json=context,
            )
        )
    elif (row.owner_kind, row.owner_job_id, row.dependency_job_id, row.context_json) != (
        owner_kind,
        owner_job_id,
        dependency_job_id,
        context,
    ):
        raise ValueError("原继续操作不能绑定不同的媒体依据")
    emit_ws_event(db, user_id=user_id, event_type="media.review.updated", payload={"job_id": str(dependency_job_id)})


async def continue_media_wait(user_id: int, operation_id: str, *, explicit: bool = False) -> dict | None:
    async with SESSION_LOCAL() as db:
        owner_id = await db.scalar(
            select(MediaContinuation.owner_job_id).where(
                MediaContinuation.user_id == user_id, MediaContinuation.operation_id == operation_id
            )
        )
        if owner_id is None or await owned_job_control(db, user_id, owner_id, lock=True) is None:
            return None
        row = await db.scalar(
            select(MediaContinuation)
            .where(MediaContinuation.user_id == user_id, MediaContinuation.operation_id == operation_id)
            .with_for_update()
        )
        if row is None or row.state == "cancelled":
            return None
        if row.state == "complete":
            return row.result_json
        if not explicit and not row.automatic:
            return None
        handler = _HANDLERS.get(row.owner_kind)
        if handler is None:
            return None
        result = await handler(db, MediaWait.model_validate(row))
        row.updated_at = utc_now()
        if result is not None:
            row.state, row.result_json = "complete", result
        await db.commit()
        return result


async def reconcile_media_waits(user_id: int | None = None, job_id: int | None = None) -> None:
    async with SESSION_LOCAL() as db:
        statement = select(MediaContinuation.user_id, MediaContinuation.operation_id).where(
            MediaContinuation.state == "waiting", MediaContinuation.automatic.is_(True)
        )
        if user_id is not None:
            statement = statement.where(MediaContinuation.user_id == user_id)
        if job_id is not None:
            statement = statement.where(MediaContinuation.dependency_job_id == job_id)
        waits = list(
            (await db.execute(statement.order_by(MediaContinuation.updated_at, MediaContinuation.id).limit(100))).all()
        )
    for owner, operation in waits:
        try:
            await continue_media_wait(owner, operation)
        except Exception:
            logger.warning(
                "Media continuation deferred", extra={"user_id": owner, "operation_id": operation}, exc_info=True
            )


async def media_review_updated(user_id: int, payload: dict) -> None:
    await reconcile_media_waits(user_id, int(payload["job_id"]))


async def available_media_actions(
    user_id: int, job_id: int
) -> list[Literal["save_outfit", "publish_post", "deliver_message"]]:
    async with SESSION_LOCAL() as db:
        kinds = list(
            await db.scalars(
                select(MediaContinuation.owner_kind).where(
                    MediaContinuation.user_id == user_id,
                    MediaContinuation.owner_job_id == job_id,
                    MediaContinuation.state == "waiting",
                    MediaContinuation.automatic.is_(False),
                )
            )
        )
    return list(
        dict.fromkeys(
            "deliver_message"
            if kind in {"chat.image", "chat.video"}
            else "save_outfit"
            if kind == "outfit.create"
            else "publish_post"
            for kind in kinds
            if kind in {"chat.image", "chat.video", "outfit.create", "post.publish"}
        )
    )


async def continue_owned_media_operation(user_id: int, job_id: int, action: str) -> None:
    owner_kinds = {
        "save_outfit": ("outfit.create",),
        "publish_post": ("post.publish",),
        "deliver_message": ("chat.image", "chat.video"),
    }[action]
    async with SESSION_LOCAL() as db:
        operations = list(
            await db.scalars(
                select(MediaContinuation.operation_id).where(
                    MediaContinuation.user_id == user_id,
                    MediaContinuation.owner_job_id == job_id,
                    MediaContinuation.owner_kind.in_(owner_kinds),
                )
            )
        )
    if not operations:
        raise ValueError("原任务没有授权这项业务操作")
    for operation in operations:
        await continue_media_wait(user_id, operation, explicit=True)
