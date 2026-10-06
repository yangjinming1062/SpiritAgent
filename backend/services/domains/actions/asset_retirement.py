"""动作资产延迟回收；登记与引用释放同事务，清理前重新检查包、尝试和复核引用。"""

from collections.abc import Iterable
from datetime import timedelta

from components import SESSION_LOCAL, begin_user_request, end_user_request, get_logger, utc_now
from modules.auth import User
from modules.companion import (
    ActionAssetRetirement,
    CompanionAction,
    CompanionActionPack,
    CompanionMediaReview,
    RemovedActionVideoTask,
    removed_video_tasks,
)
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from services.domains.assets import (
    ACTION_ASSET_FIELDS,
    PACK_ASSET_FIELDS,
    asset_paths,
    cleanup_user_assets,
    enqueue_asset_cleanup,
)
from services.infrastructure.assets import parse_companion_asset_path

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
        users = (await db.scalars(select(User.id))).all()
    for user_id in users:
        if not await begin_user_request(user_id):
            continue
        try:
            await _cleanup_user_action_assets(user_id)
        finally:
            await end_user_request(user_id)


async def _cleanup_user_action_assets(user_id: int) -> None:
    async with SESSION_LOCAL() as db:
        for retirement in await db.scalars(
            select(ActionAssetRetirement).where(ActionAssetRetirement.user_id == user_id),
        ):
            await enqueue_asset_cleanup(db, user_id, [retirement.path], not_before=retirement.retired_at + ASSET_GRACE)
        await db.commit()
    await cleanup_user_assets(user_id)


async def delete_outfit_action_packs(
    db: AsyncSession,
    user_id: int,
    outfit_id: int,
) -> list[RemovedActionVideoTask]:
    """调用方持外观锁；拒绝在用/制作中的包，其余关联包同事务删除并延迟回收；返回待撤销的远端视频句柄供调用方提交后处理。"""
    packs = (
        await db.scalars(
            select(CompanionActionPack).where(
                CompanionActionPack.user_id == user_id,
                CompanionActionPack.outfit_id == outfit_id,
            ),
        )
    ).all()
    if not packs:
        return []
    pack_ids = {pack.id for pack in packs}
    actions = (await db.scalars(select(CompanionAction).where(CompanionAction.pack_id.in_(pack_ids)))).all()
    if any(pack.active or pack.status == "processing" for pack in packs) or any(
        action.status in ("queued", "processing") for action in actions
    ):
        raise ValueError("使用中或制作中的动作包不能删除，请等待制作完成")
    paths = set().union(
        *(pack_asset_paths(pack) for pack in packs),
        *(action_asset_paths(action) for action in actions),
    )
    await retire_action_assets(db, user_id, paths)
    for review in await db.scalars(
        select(CompanionMediaReview).where(
            CompanionMediaReview.user_id == user_id,
            CompanionMediaReview.status == "pending",
        ),
    ):
        if review.publication and review.publication.get("pack_id") in pack_ids:
            review.status = "rejected"
    removed = removed_video_tasks(actions)
    for pack in packs:
        await db.delete(pack)
    return removed
