"""事务性回收登记和正式资产磁盘补偿；任务取消由应用层在提交之后组织。"""

import asyncio
import json
from collections.abc import Iterable
from contextlib import suppress
from datetime import datetime, timedelta
from pathlib import Path

from components import SESSION_LOCAL, SETTINGS, begin_user_request, end_user_request, get_logger, utc_now
from modules.auth import User
from modules.companion import ActionAssetRetirement, CompanionMediaReview, FullbodyCandidate
from modules.conversation import Message
from modules.media import AssetCleanupPending, VideoGenJob
from modules.ws import WSEvent
from sqlalchemy import delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from services.infrastructure.assets import (
    asset_write_in_progress,
    parse_companion_asset_path,
    prune_completed_asset_writes,
    unlink_companion_asset,
    user_asset_lock,
)

from .references import AssetReferencesUnavailable, asset_paths, collect_live_asset_paths, message_asset_paths

logger = get_logger(__name__)
_ACTION_GRACE = timedelta(hours=24)
_ASSET_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".json",
    ".mp4",
    ".webm",
    ".mov",
    ".mkv",
    ".mp3",
    ".wav",
    ".ogg",
    ".flac",
    ".aac",
    ".m4a",
}


async def enqueue_asset_cleanup(
    db: AsyncSession,
    user_id: int,
    paths: Iterable[str],
    *,
    not_before: datetime | None = None,
) -> None:
    owned = {path for path in paths if (parsed := parse_companion_asset_path(path)) and parsed[0] == user_id}
    if not owned:
        return
    now = utc_now()
    await db.execute(
        insert(AssetCleanupPending)
        .values(
            [
                {"user_id": user_id, "path": path, "requested_at": now, "not_before": not_before or now}
                for path in sorted(owned)
            ],
        )
        .on_conflict_do_nothing(index_elements=[AssetCleanupPending.user_id, AssetCleanupPending.path]),
    )


async def collect_message_asset_releases(
    db: AsyncSession,
    user_id: int,
    conversation_ids: Iterable[int],
    *,
    from_message_id: int | None = None,
) -> set[int]:
    """必须在删除消息之前调用；回滚同时撤销任务失效、通知删除与磁盘回收登记。"""
    conversations = set(conversation_ids)
    session_ids = {str(conversation_id) for conversation_id in conversations}
    query = select(Message).where(Message.conversation_id.in_(conversations))
    if from_message_id is not None:
        query = query.where(Message.id >= from_message_id)
    # 与视频入队共享用户源消息锁；作业完成按 job→reply 加锁，不能先锁助手行。
    await db.execute(query.where(Message.role == "user").order_by(Message.id).with_for_update())
    messages = list(await db.scalars(query))
    message_ids = {row.id for row in messages}
    if not message_ids and from_message_id is not None:
        return set()
    released = set().union(*(message_asset_paths(message, user_id) for message in messages))
    ownership = or_(VideoGenJob.source_message_id.in_(message_ids), VideoGenJob.reply_message_id.in_(message_ids))
    if from_message_id is None:
        ownership = or_(ownership, VideoGenJob.session_id.in_(session_ids))
    job_ids: set[str] = set()
    for job in await db.scalars(
        select(VideoGenJob).where(VideoGenJob.user_id == user_id, ownership).order_by(VideoGenJob.id).with_for_update(),
    ):
        job_ids.add(str(job.id))
        job.cleanup_requested_at = utc_now()
        released |= asset_paths([job.video_url, job.candidate_video_url, job.generation_state_json], user_id)
    for event in await db.scalars(select(WSEvent).where(WSEvent.user_id == user_id)):
        try:
            payload = json.loads(event.payload)
        except (ValueError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        belongs_to_removed_history = (
            payload.get("message_id") in message_ids
            or payload.get("status_message_id") in message_ids
            or event.event_type.startswith("video_gen.")
            and str(payload.get("task_id")) in job_ids
            or from_message_id is None
            and str(payload.get("session_id")) in session_ids
            and event.event_type
            in {"message.voice", "message.media", "companion.message", "video_gen.completed", "video_gen.failed"}
        )
        if belongs_to_removed_history:
            released |= asset_paths(payload, user_id)
            await db.delete(event)
    await enqueue_asset_cleanup(db, user_id, released)
    await db.flush()
    return message_ids


def _disk_assets(user_id: int) -> set[str]:
    root = Path(SETTINGS.data_dir) / "companion-assets" / str(user_id)
    if root.is_symlink() or not root.exists():
        return set()
    result: set[str] = set()
    for directory, dirs, files in root.walk(follow_symlinks=False):
        dirs[:] = [name for name in dirs if not (directory / name).is_symlink()]
        for filename in files:
            if Path(filename).suffix.lower() not in _ASSET_SUFFIXES:
                continue
            path = directory / filename
            if not path.is_symlink() and path.is_file():
                relative = path.relative_to(Path(SETTINGS.data_dir)).as_posix()
                if parse_companion_asset_path(relative):
                    result.add(relative)
    return result


def _remove_empty_directories(user_id: int) -> None:
    root = Path(SETTINGS.data_dir) / "companion-assets" / str(user_id)
    if root.is_symlink() or not root.exists():
        return
    for directory, _, _ in root.walk(top_down=False, follow_symlinks=False):
        if directory != root and not directory.is_symlink():
            # 非空目录留给仍持有其中资源的业务。
            with suppress(OSError):
                directory.rmdir()


async def _discard_obsolete_asset_records(db: AsyncSession, user_id: int, released: set[str]) -> None:
    """已采纳资源由目标业务持有；终态候选和已投递媒体事件不再持有文件。"""
    for candidate in await db.scalars(
        select(FullbodyCandidate).where(
            FullbodyCandidate.user_id == user_id,
            FullbodyCandidate.status.in_(("accepted", "rejected")),
        ),
    ):
        released |= asset_paths([candidate.image_url, candidate.base_fullbody_url], user_id)
        await db.delete(candidate)
    for review in await db.scalars(
        select(CompanionMediaReview).where(
            CompanionMediaReview.user_id == user_id,
            CompanionMediaReview.status != "pending",
            CompanionMediaReview.updated_at < utc_now() - timedelta(days=7),
        ),
    ):
        released |= asset_paths([review.media_url, review.publication], user_id)
        # 保留采纳/拒绝的幂等结果（status）；展示与归属字段一并清空，避免每轮清扫重复扫描同一路径。
        review.media_url = ""
        review.publication = None
    for event in await db.scalars(
        select(WSEvent).where(WSEvent.user_id == user_id, WSEvent.status.not_in(("PENDING", "PROCESSING"))),
    ):
        paths = asset_paths(event.payload, user_id)
        if paths:
            released |= paths
            await db.delete(event)


async def _cleanup_user_assets(user_id: int, *, discover: bool = False) -> int:
    """先持久化待办，再锁内只读核对与删除，最后保存结果；不持文件锁等待业务行锁。"""
    prune_completed_asset_writes()
    async with SESSION_LOCAL() as db:
        released: set[str] = set()
        await _discard_obsolete_asset_records(db, user_id, released)
        await db.flush()
        if discover:
            try:
                referenced = await collect_live_asset_paths(db, user_id)
            except AssetReferencesUnavailable:
                logger.warning(
                    "Asset discovery deferred: references unavailable",
                    extra={"user_id": user_id},
                    exc_info=True,
                )
            else:
                released |= await asyncio.to_thread(_disk_assets, user_id) - referenced
        retirements = list(
            await db.scalars(select(ActionAssetRetirement).where(ActionAssetRetirement.user_id == user_id)),
        )
        protected_until = {row.path: row.retired_at + _ACTION_GRACE for row in retirements}
        now = utc_now()
        for path in released:
            not_before = protected_until.get(path)
            if not_before is None and any(part.startswith("pack") and part[4:].isdigit() for part in Path(path).parts):
                not_before = now + _ACTION_GRACE
            await enqueue_asset_cleanup(db, user_id, [path], not_before=not_before)
        await db.commit()

    deleted_ids: list[int] = []
    deleted_paths: list[str] = []
    errors: dict[int, str] = {}
    async with user_asset_lock(user_id), SESSION_LOCAL() as db:
        # 一致快照防止采纳在不同表的两次查询之间转移引用，造成两端都未读到。
        if db.get_bind().dialect.name == "postgresql":
            await db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        try:
            referenced = await collect_live_asset_paths(db, user_id)
        except AssetReferencesUnavailable:
            logger.warning("Asset cleanup deferred: references unavailable", extra={"user_id": user_id}, exc_info=True)
            return 0
        pending = list(
            await db.scalars(
                select(AssetCleanupPending).where(
                    AssetCleanupPending.user_id == user_id,
                    AssetCleanupPending.not_before <= utc_now(),
                ),
            ),
        )
        for entry in pending:
            if entry.path in referenced or asset_write_in_progress(entry.path):
                continue
            try:
                await asyncio.to_thread(unlink_companion_asset, entry.path)
                if (Path(SETTINGS.data_dir) / entry.path).exists():
                    raise OSError("Asset remains on disk after cleanup")
            except OSError as exc:
                errors[entry.id] = str(exc)[:1000]
                logger.warning(
                    "Asset cleanup will retry",
                    extra={"user_id": user_id, "path": entry.path},
                    exc_info=True,
                )
            else:
                deleted_ids.append(entry.id)
                deleted_paths.append(entry.path)
        await asyncio.to_thread(_remove_empty_directories, user_id)

    # 进程在此处退出时文件已删除、待办仍在；下一次按文件不存在幂等收敛。
    async with SESSION_LOCAL() as db:
        for entry_id, error in errors.items():
            await db.execute(
                update(AssetCleanupPending)
                .where(AssetCleanupPending.id == entry_id)
                .values(
                    attempts=AssetCleanupPending.attempts + 1,
                    last_error=error,
                ),
            )
        if deleted_ids:
            await db.execute(delete(AssetCleanupPending).where(AssetCleanupPending.id.in_(deleted_ids)))
            await db.execute(
                delete(ActionAssetRetirement).where(
                    ActionAssetRetirement.user_id == user_id,
                    ActionAssetRetirement.path.in_(deleted_paths),
                ),
            )
        await db.commit()
    return len(deleted_ids)


async def cleanup_user_assets(user_id: int, *, discover: bool = False) -> int:
    """回收失败保留持久化待办，不把已成功提交的用户操作报告为失败。"""
    try:
        return await _cleanup_user_assets(user_id, discover=discover)
    except Exception:
        logger.warning("Asset cleanup deferred", extra={"user_id": user_id}, exc_info=True)
        return 0


async def cleanup_assets(*, discover: bool = False) -> None:
    async with SESSION_LOCAL() as db:
        users = list(await db.scalars(select(User.id)))
    for user_id in users:
        if not await begin_user_request(user_id):
            continue
        try:
            await cleanup_user_assets(user_id, discover=discover)
        except Exception:
            logger.warning("Asset cleanup failed", extra={"user_id": user_id}, exc_info=True)
        finally:
            await end_user_request(user_id)
