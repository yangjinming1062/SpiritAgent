"""动作库读写：pack、action、目录版本与播放意图。"""

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from features.actions.models import ActionPlayback, CompanionAction, CompanionActionPack


class StaleCatalogError(RuntimeError):
    """目录版本已被并发发布推进；`publish_action_catalog` 重读后重试，仍冲突才抛给调用方。"""


async def get_active_pack(db: AsyncSession, user_id: int) -> CompanionActionPack | None:
    row = await db.execute(
        select(CompanionActionPack).where(
            CompanionActionPack.user_id == user_id,
            CompanionActionPack.active.is_(True),
        ),
    )
    return row.scalar_one_or_none()


async def list_pack_actions(
    db: AsyncSession,
    pack_id: int,
    *,
    enabled_only: bool = True,
    refresh: bool = False,
) -> list[CompanionAction]:
    """refresh 以数据库行覆盖会话中已加载的动作，会丢弃其未 flush 的改动，调用方须先 flush。"""
    stmt = select(CompanionAction).where(CompanionAction.pack_id == pack_id)
    if enabled_only:
        stmt = stmt.where(CompanionAction.enabled.is_(True))
    if refresh:
        stmt = stmt.execution_options(populate_existing=True)
    rows = await db.execute(stmt.order_by(CompanionAction.system_slot.desc(), CompanionAction.key))
    return list(rows.scalars().all())


async def get_action(db: AsyncSession, action_id: int) -> CompanionAction | None:
    return await db.get(CompanionAction, action_id)


async def get_playback(db: AsyncSession, play_id: str) -> ActionPlayback | None:
    return (await db.execute(select(ActionPlayback).where(ActionPlayback.play_id == play_id))).scalar_one_or_none()


def clear_action_attempt(action: CompanionAction) -> None:
    """作废动作当前的生成尝试与成品，下一次制作从独立的新尝试开始；不改状态与元信息，也不删除文件。"""
    action.provider_task_id = None
    action.artifact_path = None
    action.pose_path = None
    action.generation_state_json = None
    action.pose_generation_state_json = None
    action.script_json = None
    action.result_json = None
    action.peek_geometry_json = None
    action.content_rect_json = None
    action.media_path = ""
    action.media_hash = ""
    action.cover_path = None
    action.hitmask_path = None
    action.actual_duration_ms = None
    action.frames = None
    action.hitmask_grid_w = None
    action.hitmask_grid_h = None
    action.hitmask_fps = None


async def publish_catalog(
    db: AsyncSession,
    pack: CompanionActionPack,
    *,
    manifest_path: str,
    content_hash: str,
) -> int:
    """CAS 推进目录版本：仅当版本指针未被并发发布推进时落库，失败抛 StaleCatalogError。"""
    next_version = pack.catalog_version + 1
    result = await db.execute(
        update(CompanionActionPack)
        .where(
            CompanionActionPack.id == pack.id,
            CompanionActionPack.catalog_version == pack.catalog_version,
        )
        .values(
            catalog_version=next_version,
            manifest_path=manifest_path,
            content_hash=content_hash,
        ),
    )
    if result.rowcount != 1:
        raise StaleCatalogError(f"pack {pack.id} catalog version advanced concurrently")
    # 与 UPDATE 的 VALUES 对齐写一次；SQLAlchemy 可能已同步 ORM，禁止再 += 1。
    pack.catalog_version = next_version
    pack.manifest_path = manifest_path
    pack.content_hash = content_hash
    await db.flush()
    return pack.catalog_version


async def record_playback(
    db: AsyncSession,
    *,
    user_id: int,
    play_id: str,
    pack_id: int,
    action_id: int,
    appearance_epoch: int,
    source: str,
    expires_at: datetime,
) -> ActionPlayback:
    """创建播放意图记录；终态由 usage.record_play_result 更新。"""
    entry = ActionPlayback(
        user_id=user_id,
        play_id=play_id,
        pack_id=pack_id,
        action_id=action_id,
        appearance_epoch=appearance_epoch,
        source=source,
        expires_at=expires_at,
        status="queued",
    )
    db.add(entry)
    await db.flush()
    return entry
