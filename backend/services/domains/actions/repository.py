"""动作库读写：pack、action、目录版本与播放意图。"""

import hashlib
import json
from datetime import datetime

from modules.companion import ActionPlayback, CompanionAction, CompanionActionPack
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession


class StaleCatalogError(RuntimeError):
    """并发发布推进了目录版本；调用方重读目录后重试发布。"""


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
) -> list[CompanionAction]:
    stmt = select(CompanionAction).where(CompanionAction.pack_id == pack_id)
    if enabled_only:
        stmt = stmt.where(CompanionAction.enabled.is_(True))
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


async def upsert_action(
    db: AsyncSession,
    *,
    user_id: int | None = None,
    pack_id: int,
    key: str,
    name: str,
    system_slot: str = "",
    kind: str = "once",
    motion_description: str = "",
    use_when: list[str] | None = None,
    avoid_when: list[str] | None = None,
    tags: list[str] | None = None,
) -> CompanionAction:
    """创建或更新动作元信息；仅元信息变更递增 metadata_revision，不重新生成视频。"""
    existing = await get_action_by_key(db, pack_id, key)
    if existing:
        existing.name = name
        existing.kind = kind
        existing.motion_description = motion_description
        existing.use_when = json.dumps(use_when or [], ensure_ascii=False)
        existing.avoid_when = json.dumps(avoid_when or [], ensure_ascii=False)
        existing.tags = json.dumps(tags or [], ensure_ascii=False)
        existing.metadata_revision += 1
        await db.flush()
        return existing

    if user_id is None:
        pack = await db.get(CompanionActionPack, pack_id)
        user_id = pack.user_id if pack else 0

    action = CompanionAction(
        user_id=user_id,
        pack_id=pack_id,
        key=key,
        name=name,
        system_slot=system_slot,
        kind=kind,
        motion_description=motion_description,
        use_when=json.dumps(use_when or [], ensure_ascii=False),
        avoid_when=json.dumps(avoid_when or [], ensure_ascii=False),
        tags=json.dumps(tags or [], ensure_ascii=False),
    )
    db.add(action)
    await db.flush()
    return action


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
    expires_at: datetime | None,
    repeat_count: int = 1,
) -> None:
    """创建播放意图记录；终态由 usage.record_play_result 更新。"""
    entry = ActionPlayback(
        user_id=user_id,
        play_id=play_id,
        pack_id=pack_id,
        action_id=action_id,
        appearance_epoch=appearance_epoch,
        source=source,
        expires_at=expires_at,
        repeat_count=repeat_count,
        status="queued",
    )
    db.add(entry)
    await db.flush()
