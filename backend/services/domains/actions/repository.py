"""动作库读写：pack、action、目录版本与播放意图。"""

import hashlib
import json
import unicodedata
from datetime import datetime

from modules.companion import ActionPlayback, CompanionAction, CompanionActionPack
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .materials import preserve_action_asset


class StaleCatalogError(RuntimeError):
    """目录版本已被并发发布推进；`publish_action_catalog` 重读后重试，仍冲突才抛给调用方。"""


class ActionNameConflictError(ValueError):
    """遗留同名多动作不能静默合并、重命名或覆盖。"""


def normalize_action_name(name: str) -> str:
    return unicodedata.normalize("NFKC", name.strip()).casefold()


async def get_action_by_name(db: AsyncSession, pack_id: int, name: str) -> CompanionAction | None:
    expected = normalize_action_name(name)
    matches = [
        action
        for action in await list_pack_actions(db, pack_id, enabled_only=False)
        if not action.system_slot and normalize_action_name(action.name) == expected
    ]
    if len(matches) > 1:
        raise ActionNameConflictError("存在多个同名动作，请先删除重复动作后重新提案")
    return matches[0] if matches else None


def make_semantic_fingerprint(name: str, motion: str) -> str:
    raw = name.strip().lower() + "|" + motion.strip().lower()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


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


async def get_action_by_key(
    db: AsyncSession,
    pack_id: int,
    key: str,
) -> CompanionAction | None:
    row = await db.execute(
        select(CompanionAction).where(
            CompanionAction.pack_id == pack_id,
            CompanionAction.key == key,
        ),
    )
    return row.scalar_one_or_none()


async def create_action(
    db: AsyncSession,
    *,
    user_id: int,
    pack_id: int,
    key: str,
    name: str,
    kind: str,
    motion_description: str,
    use_when: list[str],
    avoid_when: list[str],
) -> CompanionAction:
    """新建动作行；同包同 key 已有动作时由唯一约束拒绝，不覆盖其他提案的动作。"""
    action = CompanionAction(
        user_id=user_id,
        pack_id=pack_id,
        key=key,
        name=name,
        kind=kind,
        motion_description=motion_description,
        use_when=json.dumps(use_when, ensure_ascii=False),
        avoid_when=json.dumps(avoid_when, ensure_ascii=False),
    )
    db.add(action)
    await db.flush()
    return action


def clear_action_attempt(action: CompanionAction) -> None:
    """作废动作当前的生成尝试与成品，下一次制作从独立的新尝试开始；不改状态与元信息，也不删除文件。"""
    preserve_action_asset(action)
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
