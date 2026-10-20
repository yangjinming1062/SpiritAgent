"""按已保存的实际画面补全桌面背景默认名称。"""

import asyncio
from dataclasses import dataclass
from datetime import datetime

from infrastructure.ai.error_classifier import LlmCallBlockedError
from infrastructure.ai.prompt_engineer import vision_chat_json
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.assets.asset_store import read_valid_image_data_uri
from infrastructure.assets.paths import parse_companion_asset_path
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from infrastructure.text import resolve_language
from pydantic import BaseModel, ConfigDict, Field, field_validator
from runtime.background import TaskBag
from runtime.paid_work import require_new_generation_call
from runtime.user_maintenance import is_user_in_maintenance
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from features.accounts.locking import lock_account
from features.configuration.settings.values import get_user_setting

from .models import DesktopVideoAction, DesktopVideoSet
from .prompts import DESKTOP_BACKGROUND_TITLE
from .repository import desktop_accepted_asset, desktop_progress, touch_desktop_state

_DEFAULT_TITLES = ("", "我的桌面背景", "My desktop background")
_PENDING: dict[int, dict[int, None]] = {}
_WORKERS: dict[int, asyncio.Task[None]] = {}
_TASKS = TaskBag("desktop_life.naming")
_STOPPING = False
logger = get_logger(__name__)


class _BackgroundTitle(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=160, pattern=r"^[^\x00]*$", strict=True)

    @field_validator("title")
    @classmethod
    def descriptive_title(cls, value: str) -> str:
        if value.casefold() in {title.casefold() for title in (*_DEFAULT_TITLES, "桌面背景", "Desktop background")}:
            raise ValueError("名称需要体现实际画面特征")
        return value


@dataclass(frozen=True)
class _NamingInput:
    user_id: int
    set_id: int
    title: str
    updated_at: datetime
    image_path: str
    action_version: int
    resolution_revision: int
    language: str


async def _naming_input(db: AsyncSession, user_id: int, set_id: int) -> _NamingInput | None:
    suite = await db.scalar(
        select(DesktopVideoSet).where(DesktopVideoSet.user_id == user_id, DesktopVideoSet.id == set_id),
    )
    if suite is None or suite.title.strip() not in _DEFAULT_TITLES:
        return None
    action = await db.scalar(
        select(DesktopVideoAction).where(
            DesktopVideoAction.user_id == user_id,
            DesktopVideoAction.set_id == set_id,
            DesktopVideoAction.key == "idle",
        ),
    )
    if action is None:
        return None
    accepted = desktop_accepted_asset(action)
    if accepted is not None:
        image_path = accepted.image_path
    else:
        progress = desktop_progress(action)
        if progress is None or progress.resolution_revision != suite.resolution_revision:
            return None
        if not progress.image_confirmed and not (
            action.status == "review_pending"
            and action.stage in {"image_review", "image_confirmed", "review"}
            and not action.error
            and not progress.failure_reason
        ):
            return None
        image_path = progress.candidate.image_path if progress.candidate else progress.pose_path
    parsed = parse_companion_asset_path(image_path)
    if parsed is None or parsed[0] != user_id:
        return None
    return _NamingInput(
        user_id=user_id,
        set_id=set_id,
        title=suite.title,
        updated_at=suite.updated_at,
        image_path=image_path,
        action_version=action.version,
        resolution_revision=suite.resolution_revision,
        language=resolve_language(await get_user_setting(db, user_id, "language")),
    )


async def _name_background(user_id: int, set_id: int, attempted: set[_NamingInput]) -> None:
    require_new_generation_call(user_id)
    async with SESSION_LOCAL() as db:
        await lock_account(db, user_id)
        expected = await _naming_input(db, user_id, set_id)
    if expected is None or expected in attempted:
        return
    attempted.add(expected)
    reference = await asyncio.to_thread(read_valid_image_data_uri, expected.image_path)
    if not reference:
        logger.warning("Desktop background naming image unavailable", extra={"user_id": user_id, "set_id": set_id})
        return

    async def before_submit() -> None:
        require_new_generation_call(user_id)
        async with SESSION_LOCAL() as db:
            await lock_account(db, user_id)
            if await _naming_input(db, user_id, set_id) != expected:
                raise LlmCallBlockedError("桌面背景名称或画面已变化")

    result = await vision_chat_json(
        user_id,
        DESKTOP_BACKGROUND_TITLE,
        {"output_language": expected.language},
        response_model=_BackgroundTitle,
        schema_name="desktop_background_title",
        reference_images=(reference,),
        before_submit=before_submit,
        task=AiTaskKey.desktop_life,
    )
    require_new_generation_call(user_id)
    async with SESSION_LOCAL() as db:
        await lock_account(db, user_id)
        if await _naming_input(db, user_id, set_id) != expected:
            return
        suite = await db.get(DesktopVideoSet, set_id)
        if suite is None:
            return
        suite.title = result.title
        await touch_desktop_state(db, user_id)
        await db.commit()


async def _run_naming_jobs(user_id: int) -> None:
    attempted: set[_NamingInput] = set()
    while pending := _PENDING.get(user_id):
        set_id = next(iter(pending))
        pending.pop(set_id)
        try:
            await _name_background(user_id, set_id, attempted)
        except LlmCallBlockedError:
            continue
        except Exception:
            logger.warning(
                "Desktop background naming failed",
                extra={"user_id": user_id, "set_id": set_id},
                exc_info=True,
            )


def schedule_desktop_title(user_id: int, set_id: int) -> None:
    if _STOPPING or is_user_in_maintenance(user_id):
        return
    _PENDING.setdefault(user_id, {})[set_id] = None
    previous = _WORKERS.get(user_id)
    if previous is not None and not previous.done():
        return
    task = asyncio.create_task(_run_naming_jobs(user_id), name=f"desktop-background-naming-{user_id}")
    _WORKERS[user_id] = task
    _TASKS.add(task, user_id=user_id)

    def finished(done: asyncio.Task[None]) -> None:
        if _WORKERS.get(user_id) is done:
            _WORKERS.pop(user_id, None)
            _PENDING.pop(user_id, None)

    task.add_done_callback(finished)


async def resume_desktop_title_jobs(user_id: int | None = None) -> None:
    global _STOPPING
    if user_id is None:
        _STOPPING = False
    async with SESSION_LOCAL() as db:
        query = select(DesktopVideoSet.user_id, DesktopVideoSet.id).where(
            func.trim(DesktopVideoSet.title).in_(_DEFAULT_TITLES),
        )
        if user_id is not None:
            query = query.where(DesktopVideoSet.user_id == user_id)
        rows = list(await db.execute(query.order_by(DesktopVideoSet.user_id, DesktopVideoSet.id)))
    for owner_id, set_id in rows:
        schedule_desktop_title(owner_id, set_id)


async def stop_desktop_title_jobs() -> None:
    global _STOPPING
    _STOPPING = True


async def drain_desktop_title_jobs() -> None:
    await stop_desktop_title_jobs()
    await _TASKS.drain()
