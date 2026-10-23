"""按实际画面补全影像名称。"""

import asyncio
from dataclasses import dataclass

from infrastructure.ai.error_classifier import LlmCallBlockedError
from infrastructure.ai.prompt_engineer import vision_chat_json
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.assets.asset_store import read_valid_image_data_uri
from infrastructure.assets.paths import parse_companion_asset_path
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from infrastructure.text import resolve_language
from pydantic import BaseModel, ConfigDict, Field
from runtime.background import TaskBag
from runtime.paid_work import require_new_generation_call
from runtime.user_maintenance import is_user_in_maintenance
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.accounts.locking import lock_account
from features.configuration.settings.values import get_user_setting

from .models import VisualItem
from .prompts import VISUAL_TITLE
from .repository import notify_visuals_updated

_PENDING: dict[int, dict[int, None]] = {}
_WORKERS: dict[int, asyncio.Task[None]] = {}
_TASKS = TaskBag("visuals.naming")
_STOPPING = False
logger = get_logger(__name__)


class _VisualTitle(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=160, pattern=r"^[^\x00]*$", strict=True)


@dataclass(frozen=True)
class _NamingInput:
    user_id: int
    visual_id: int
    image_path: str
    version: int
    language: str


def _reference_image(item: VisualItem) -> str:
    if item.media_kind == "image":
        return item.media_path
    return item.input_image_path or item.poster_path or item.media_path


async def _naming_input(db: AsyncSession, user_id: int, visual_id: int) -> _NamingInput | None:
    row = await db.scalar(
        select(VisualItem).where(VisualItem.user_id == user_id, VisualItem.id == visual_id),
    )
    if row is None or row.title.strip():
        return None
    image_path = _reference_image(row)
    parsed = parse_companion_asset_path(image_path)
    if parsed is None or parsed[0] != user_id:
        return None
    return _NamingInput(
        user_id=user_id,
        visual_id=visual_id,
        image_path=image_path,
        version=row.version,
        language=resolve_language(await get_user_setting(db, user_id, "language")),
    )


async def _name_visual(user_id: int, visual_id: int, attempted: set[_NamingInput]) -> None:
    require_new_generation_call(user_id)
    async with SESSION_LOCAL() as db:
        await lock_account(db, user_id)
        expected = await _naming_input(db, user_id, visual_id)
    if expected is None or expected in attempted:
        return
    attempted.add(expected)
    reference = await asyncio.to_thread(read_valid_image_data_uri, expected.image_path)
    if not reference:
        logger.warning("Visual naming image unavailable", extra={"user_id": user_id, "visual_id": visual_id})
        return

    async def before_submit() -> None:
        require_new_generation_call(user_id)
        async with SESSION_LOCAL() as db:
            await lock_account(db, user_id)
            if await _naming_input(db, user_id, visual_id) != expected:
                raise LlmCallBlockedError("影像名称或画面已变化")

    result = await vision_chat_json(
        user_id,
        VISUAL_TITLE,
        {"output_language": expected.language},
        response_model=_VisualTitle,
        schema_name="visual_title",
        reference_images=(reference,),
        before_submit=before_submit,
        task=AiTaskKey.visuals,
    )
    require_new_generation_call(user_id)
    async with SESSION_LOCAL() as db:
        await lock_account(db, user_id)
        if await _naming_input(db, user_id, visual_id) != expected:
            return
        row = await db.get(VisualItem, visual_id)
        if row is None:
            return
        if not row.title.strip():
            row.title = result.title
        await notify_visuals_updated(db, user_id)
        await db.commit()


async def _run_naming_jobs(user_id: int) -> None:
    attempted: set[_NamingInput] = set()
    while pending := _PENDING.get(user_id):
        visual_id = next(iter(pending))
        pending.pop(visual_id)
        try:
            await _name_visual(user_id, visual_id, attempted)
        except LlmCallBlockedError:
            continue
        except Exception:
            logger.warning("Visual naming failed", extra={"user_id": user_id, "visual_id": visual_id}, exc_info=True)


def schedule_visual_title(user_id: int, visual_id: int) -> None:
    if _STOPPING or is_user_in_maintenance(user_id):
        return
    _PENDING.setdefault(user_id, {})[visual_id] = None
    previous = _WORKERS.get(user_id)
    if previous is not None and not previous.done():
        return
    task = asyncio.create_task(_run_naming_jobs(user_id), name=f"visual-naming-{user_id}")
    _WORKERS[user_id] = task
    _TASKS.add(task, user_id=user_id)

    def finished(done: asyncio.Task[None]) -> None:
        if _WORKERS.get(user_id) is done:
            _WORKERS.pop(user_id, None)
            _PENDING.pop(user_id, None)

    task.add_done_callback(finished)


async def resume_visual_title_jobs(user_id: int | None = None) -> None:
    global _STOPPING
    if user_id is None:
        _STOPPING = False
    async with SESSION_LOCAL() as db:
        query = select(VisualItem.user_id, VisualItem.id).where(VisualItem.title == "")
        if user_id is not None:
            query = query.where(VisualItem.user_id == user_id)
        rows = list(await db.execute(query.order_by(VisualItem.user_id, VisualItem.id)))
    for owner_id, visual_id in rows:
        schedule_visual_title(owner_id, visual_id)


async def stop_visual_title_jobs() -> None:
    global _STOPPING
    _STOPPING = True


async def drain_visual_title_jobs() -> None:
    await stop_visual_title_jobs()
    await _TASKS.drain()
