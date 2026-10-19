"""事务性回收登记和正式资产磁盘补偿；任务取消由应用层在提交之后组织。"""

import asyncio
from collections.abc import Iterable
from contextlib import suppress
from datetime import datetime, timedelta
from pathlib import Path

from infrastructure.assets.asset_store import unlink_companion_asset
from infrastructure.assets.paths import parse_companion_asset_path, valid_asset_relative_path
from infrastructure.assets.write_protection import (
    asset_write_in_progress,
    prune_completed_asset_writes,
    user_asset_lock,
)
from infrastructure.async_io import run_blocking
from infrastructure.config import SETTINGS
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from infrastructure.text import utc_now
from runtime.user_maintenance import track_user_task
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from features.assets.contributions import cleanup_obsolete_references, forget_deleted_references, protected_asset_paths
from features.assets.models import AssetCleanupPending
from features.assets.references import (
    AssetReferencesUnavailable,
    collect_live_asset_paths,
)

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


async def enqueue_asset_cleanup_entries(
    db: AsyncSession,
    user_id: int,
    entries: Iterable[tuple[str, datetime | None]],
    *,
    expedite: bool = False,
) -> None:
    owned = {
        path: not_before
        for path, not_before in entries
        if (parsed := parse_companion_asset_path(path)) and parsed[0] == user_id
    }
    if not owned:
        return
    now = utc_now()
    statement = insert(AssetCleanupPending).values(
        [
            {"user_id": user_id, "path": path, "requested_at": now, "not_before": not_before or now}
            for path, not_before in sorted(owned.items())
        ],
    )
    keys = [AssetCleanupPending.user_id, AssetCleanupPending.path]
    await db.execute(
        statement.on_conflict_do_update(
            index_elements=keys,
            set_={"not_before": func.least(AssetCleanupPending.not_before, statement.excluded.not_before)},
        )
        if expedite
        else statement.on_conflict_do_nothing(index_elements=keys),
    )


async def enqueue_asset_cleanup(
    db: AsyncSession,
    user_id: int,
    paths: Iterable[str],
    *,
    not_before: datetime | None = None,
    expedite: bool = False,
) -> None:
    await enqueue_asset_cleanup_entries(db, user_id, ((path, not_before) for path in paths), expedite=expedite)


def _disk_assets(user_id: int, directory: str = "") -> set[str]:
    if user_id <= 0 or (directory and not valid_asset_relative_path(directory)):
        raise ValueError("invalid asset directory")
    root = Path(SETTINGS.data_dir) / "companion-assets" / str(user_id)
    if root.is_symlink() or not root.exists():
        return set()
    scan_root = root
    for part in directory.split("/") if directory else ():
        scan_root /= part
        if scan_root.is_symlink() or not scan_root.exists():
            return set()
    result: set[str] = set()
    for folder, dirs, files in scan_root.walk(follow_symlinks=False):
        dirs[:] = [name for name in dirs if not (folder / name).is_symlink()]
        for filename in files:
            if Path(filename).suffix.lower() not in _ASSET_SUFFIXES:
                continue
            path = folder / filename
            if not path.is_symlink() and path.is_file():
                relative = path.relative_to(Path(SETTINGS.data_dir)).as_posix()
                if parse_companion_asset_path(relative):
                    result.add(relative)
    return result


async def collect_asset_directory_paths(user_id: int, directory: str) -> set[str]:
    """只枚举指定账户内的专属目录；是否可删除仍由统一引用复查决定。"""
    if not directory:
        raise ValueError("asset directory is required")
    return await asyncio.to_thread(_disk_assets, user_id, directory)


async def cleanup_asset_directories(user_id: int, directories: Iterable[str]) -> None:
    """删除提交和任务收尾后补扫专属目录，磁盘或数据库故障由定时发现补偿。"""
    try:
        paths: set[str] = set()
        for directory in directories:
            paths |= await collect_asset_directory_paths(user_id, directory)
        async with SESSION_LOCAL() as db:
            await enqueue_asset_cleanup(db, user_id, paths, expedite=True)
            await db.commit()
    except Exception:
        logger.warning("Asset directory cleanup deferred", extra={"user_id": user_id}, exc_info=True)
    await cleanup_user_assets(user_id)


def cleanup_asset_directories_after_task(user_id: int, directories: Iterable[str]) -> None:
    """等待当前写入所有者退出后清理，避免本次请求自身的写入保护阻止回收。"""
    owned = tuple(directories)
    task = asyncio.current_task()
    if task is None:
        raise RuntimeError("asset cleanup requires a task owner")

    def finished(_task: asyncio.Task) -> None:
        track_user_task(
            user_id,
            asyncio.create_task(cleanup_asset_directories(user_id, owned), name=f"asset-cleanup-{user_id}"),
            cancel_on_maintenance=False,
        )

    task.add_done_callback(finished)


def _remove_empty_directories(user_id: int) -> None:
    root = Path(SETTINGS.data_dir) / "companion-assets" / str(user_id)
    if root.is_symlink() or not root.exists():
        return
    for directory, _, _ in root.walk(top_down=False, follow_symlinks=False):
        if directory != root and not directory.is_symlink():
            # 非空目录留给仍持有其中资源的业务。
            with suppress(OSError):
                directory.rmdir()


async def _cleanup_user_assets(user_id: int, *, discover: bool = False) -> int:
    """先持久化待办，再锁内只读核对与删除，最后保存结果；不持文件锁等待业务行锁。"""
    prune_completed_asset_writes()
    async with SESSION_LOCAL() as db:
        released = await cleanup_obsolete_references(db, user_id)
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
        protected_until = await protected_asset_paths(db, user_id)
        now = utc_now()
        entries: list[tuple[str, datetime | None]] = []
        for path in released:
            not_before = protected_until.get(path)
            if not_before is None and any(part.startswith("pack") and part[4:].isdigit() for part in Path(path).parts):
                not_before = now + _ACTION_GRACE
            entries.append((path, not_before))
        await enqueue_asset_cleanup_entries(db, user_id, entries)
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
                await run_blocking(unlink_companion_asset, entry.path)
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
        await run_blocking(_remove_empty_directories, user_id)

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
            await forget_deleted_references(db, user_id, deleted_paths)
        await db.commit()
    return len(deleted_ids)


async def cleanup_user_assets(user_id: int, *, discover: bool = False) -> int:
    """回收失败保留持久化待办，不把已成功提交的用户操作报告为失败。"""
    try:
        return await _cleanup_user_assets(user_id, discover=discover)
    except Exception:
        logger.warning("Asset cleanup deferred", extra={"user_id": user_id}, exc_info=True)
        return 0
