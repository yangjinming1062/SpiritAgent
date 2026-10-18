"""动作包公开操作与交付 DTO；制作模型留在动作模块。"""

from sqlalchemy.ext.asyncio import AsyncSession

from features.actions.generation import commands as _commands
from features.actions.generation.catalog import load_pack_response
from features.actions.generation.execution_state import _DEFAULT_CANVAS
from features.actions.media_contracts import VideoPackResponse
from features.actions.models import CompanionActionPack


def _summary(pack: CompanionActionPack) -> VideoPackResponse:
    return VideoPackResponse(
        id=pack.id,
        outfit_id=pack.outfit_id,
        status=pack.status,
        active=pack.active,
    )


async def create_pack_from_clips_view(
    db: AsyncSession,
    user_id: int,
    *,
    outfit_id: int,
    clips: dict[str, tuple[bytes, str]],
    canvas: tuple[int, int] = _DEFAULT_CANVAS,
    action_ranges: dict[str, tuple[float, float]] | None = None,
) -> VideoPackResponse:
    return _summary(
        await _commands.create_pack_from_clips(
            db=db,
            user_id=user_id,
            outfit_id=outfit_id,
            clips=clips,
            canvas=canvas,
            action_ranges=action_ranges,
        ),
    )


async def create_pack_from_reference_view(
    db: AsyncSession,
    user_id: int,
    *,
    outfit_id: int | None = None,
    force: bool = False,
    source_pack_id: int | None = None,
    action: str | None = None,
    feedback: str = "",
) -> VideoPackResponse:
    return _summary(
        await _commands.create_pack_from_reference(
            db=db,
            user_id=user_id,
            outfit_id=outfit_id,
            force=force,
            source_pack_id=source_pack_id,
            action=action,
            feedback=feedback,
        ),
    )


async def retry_pack_view(
    db: AsyncSession,
    user_id: int,
    pack_id: int,
    *,
    action: str | None = None,
) -> VideoPackResponse:
    return _summary(await _commands.retry_pack(db=db, user_id=user_id, pack_id=pack_id, action=action))


async def ensure_system_action_view(db: AsyncSession, user_id: int, pack_id: int, action: str) -> VideoPackResponse:
    return _summary(await _commands.ensure_system_action(db=db, user_id=user_id, pack_id=pack_id, action=action))


async def activate_pack_view(db: AsyncSession, user_id: int, pack_id: int) -> VideoPackResponse:
    pack = await _commands.activate_pack(db=db, user_id=user_id, pack_id=pack_id)
    return await load_pack_response(db, pack)
