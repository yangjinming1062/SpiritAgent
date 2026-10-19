"""动作资产退役与删除；登记与引用释放同事务，清理前重新检查包、尝试和复核引用。"""

from collections.abc import Iterable
from datetime import timedelta
from pathlib import Path

from infrastructure.assets.asset_store import outfit_asset_directory
from infrastructure.assets.paths import parse_companion_asset_path
from infrastructure.assets.references import asset_paths
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from infrastructure.text import utc_now
from runtime.user_maintenance import user_request_scope
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from features.accounts.models import lock_user_row
from features.accounts.queries import list_user_ids
from features.action_library.service import remove_container_targets
from features.actions.asset_references import catalog_asset_paths
from features.actions.generation.queries import _has_pending_work
from features.actions.models import ActionAssetRetirement, CompanionAction, CompanionActionPack, removed_video_tasks
from features.assets.lifecycle import (
    cleanup_user_assets,
    collect_asset_directory_paths,
    enqueue_asset_cleanup,
    enqueue_asset_cleanup_entries,
)
from features.assets.references import ACTION_ASSET_FIELDS, PACK_ASSET_FIELDS, AssetReferencesUnavailable
from features.media.contracts import RemovedActionVideoTask
from features.media.review_service import release_action_reviews_for_packs

ASSET_GRACE = timedelta(hours=24)
logger = get_logger(__name__)


def action_asset_paths(action: CompanionAction) -> set[str]:
    """与在线引用扫描同口径；只解析资产字段中的结构，不把脚本、人设或用户反馈当作文件引用。"""
    return asset_paths([getattr(action, field) for field in ACTION_ASSET_FIELDS], action.user_id)


def pack_asset_paths(pack: CompanionActionPack) -> set[str]:
    return asset_paths([getattr(pack, field) for field in PACK_ASSET_FIELDS], pack.user_id)


async def retire_action_assets(db: AsyncSession, user_id: int, paths: Iterable[str]) -> None:
    owned = {path for path in paths if (parsed := parse_companion_asset_path(path)) and parsed[0] == user_id}
    if not owned:
        return
    values = [{"user_id": user_id, "path": path, "retired_at": utc_now()} for path in sorted(owned)]
    statement = insert(ActionAssetRetirement).values(values)
    await db.execute(
        statement.on_conflict_do_update(
            index_elements=[ActionAssetRetirement.user_id, ActionAssetRetirement.path],
            set_={"retired_at": statement.excluded.retired_at},
        ),
    )
    await enqueue_asset_cleanup(db, user_id, owned, not_before=utc_now() + ASSET_GRACE)


async def cleanup_retired_action_assets() -> None:
    """启动后及每小时 await；不启动制作、不取消在途任务，维护中的用户留待下轮。"""
    async with SESSION_LOCAL() as db:
        users = await list_user_ids(db)
    for user_id in users:
        async with user_request_scope(user_id) as acquired:
            if not acquired:
                continue
            await _cleanup_user_action_assets(user_id)


async def _cleanup_user_action_assets(user_id: int) -> None:
    async with SESSION_LOCAL() as db:
        retirements = list(
            await db.scalars(select(ActionAssetRetirement).where(ActionAssetRetirement.user_id == user_id)),
        )
        await enqueue_asset_cleanup_entries(
            db,
            user_id,
            [(row.path, row.retired_at + ASSET_GRACE) for row in retirements],
        )
        await db.commit()
    await cleanup_user_assets(user_id)


async def delete_outfit_action_packs(
    db: AsyncSession,
    user_id: int,
    outfit_id: int,
) -> list[RemovedActionVideoTask]:
    """调用方持外观锁；在用、制作或结果未确定的动作组保留，其余同事务删除并立即登记回收。"""
    await lock_user_row(db, user_id)
    packs = (
        await db.scalars(
            select(CompanionActionPack).where(
                CompanionActionPack.user_id == user_id,
                CompanionActionPack.outfit_id == outfit_id,
            ),
        )
    ).all()
    pack_ids = {pack.id for pack in packs}
    actions = (await db.scalars(select(CompanionAction).where(CompanionAction.pack_id.in_(pack_ids)))).all()
    if any(pack.active or pack.status == "processing" or _has_pending_work(pack.id, actions) for pack in packs):
        raise ValueError("使用中、制作中或结果待核对的动作不能随着装删除")
    paths = set().union(
        *(pack_asset_paths(pack) for pack in packs),
        *(action_asset_paths(action) for action in actions),
    )
    directory = outfit_asset_directory(outfit_id)
    paths |= await collect_asset_directory_paths(user_id, directory)
    prefix = f"companion-assets/{user_id}/{directory}/"
    retired_paths = set(
        await db.scalars(
            select(ActionAssetRetirement.path).where(
                ActionAssetRetirement.user_id == user_id,
                ActionAssetRetirement.path.startswith(prefix),
            ),
        ),
    )
    paths |= retired_paths
    directory_pack_ids: set[int] = set()
    for path in paths:
        if not path.startswith(prefix):
            continue
        segment, separator, _ = path.removeprefix(prefix).partition("/")
        if separator and segment.startswith("pack") and segment[4:].isdigit() and int(segment[4:]) > 0:
            directory_pack_ids.add(int(segment[4:]))
    existing_pack_ids = set(
        await db.scalars(
            select(CompanionActionPack.id).where(CompanionActionPack.id.in_(directory_pack_ids)),
        ),
    )
    review_pack_ids = pack_ids | (directory_pack_ids - existing_pack_ids)
    preserved_prefixes = tuple(f"{prefix}pack{pack_id}/" for pack_id in existing_pack_ids - pack_ids)
    preserved_paths = {path for path in paths if path.startswith(preserved_prefixes)}
    paths -= preserved_paths
    retired_paths -= preserved_paths
    catalogs = {pack.manifest_path for pack in packs if pack.manifest_path}
    catalogs |= {path for path in paths if Path(path).name.startswith("action-catalog-") and path.endswith(".json")}
    for catalog in catalogs:
        try:
            paths |= await catalog_asset_paths(catalog, user_id)
        except AssetReferencesUnavailable:
            logger.warning(
                "Deleted action catalog unavailable; pack directory cleanup retained",
                extra={"user_id": user_id, "path": catalog},
                exc_info=True,
            )
    # 覆盖已删除旧包的退役目录；其他着装或独立动作组的保护继续保留。
    if retired_paths:
        await db.execute(
            delete(ActionAssetRetirement).where(
                ActionAssetRetirement.user_id == user_id,
                ActionAssetRetirement.path.in_(retired_paths),
            ),
        )
    paths |= await release_action_reviews_for_packs(db, user_id, review_pack_ids)
    await enqueue_asset_cleanup(db, user_id, paths, expedite=True)
    removed = removed_video_tasks(actions)
    for pack in packs:
        await remove_container_targets(db, user_id, "window", pack.id)
        await db.delete(pack)
    return removed
