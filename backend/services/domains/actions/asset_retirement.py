"""动作资产延迟回收；登记与引用释放同事务，清理前重新检查包、尝试和复核引用。"""

import asyncio
import json
import re
from collections.abc import Iterable
from datetime import timedelta
from pathlib import Path

from components import SESSION_LOCAL, SETTINGS, begin_user_request, end_user_request, get_logger, utc_now
from modules.auth import User
from modules.companion import (
    ActionAssetRetirement,
    AvatarAsset,
    CompanionAction,
    CompanionActionPack,
    CompanionMediaReview,
    CompanionOutfit,
)
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from services.infrastructure.assets import parse_companion_asset_path, unlink_companion_asset

ASSET_GRACE = timedelta(hours=24)
REVIEW_ASSET_RETENTION = timedelta(days=7)
logger = get_logger(__name__)
_LEGACY_ACTION_ASSET = re.compile(
    r"^(?:action-catalog-.+\.json|action_[a-f0-9]{32}_a\d+\.(?:mp4|webm|mov|mkv)|"
    r"action_pose_[a-f0-9]{32}\.png|video_[A-Za-z0-9_-]+_[A-Za-z0-9_-]+\.(?:webm|png|jpg|jpeg|webp|gif|json))$",
)


def _legacy_action_assets(user_id: int) -> set[str]:
    directory = Path(SETTINGS.data_dir) / "companion-assets" / str(user_id)
    if not directory.is_dir():
        return set()
    files = [path for path in directory.iterdir() if path.is_file() and not path.is_symlink()]
    pose_ids = {path.stem.removeprefix("action_pose_") for path in files if path.name.startswith("action_pose_")}
    result = set()
    cutoff = (utc_now() - ASSET_GRACE).timestamp()
    for path in files:
        # 普通 image_* 由多条图片链共用；只有同 generation_id 的动作姿态图能证明归属。
        image = re.fullmatch(r"image_([a-f0-9]{32})_a\d+_s\d+\.(?:png|jpg|webp|gif)", path.name)
        owned = bool(_LEGACY_ACTION_ASSET.fullmatch(path.name) or image and image[1] in pose_ids)
        if not owned:
            continue
        try:
            modified = path.stat().st_mtime
        except FileNotFoundError:
            continue
        if modified < cutoff:
            result.add(f"companion-assets/{user_id}/{path.name}")
    return result


def _paths(value: object) -> set[str]:
    """只解析资产字段中的结构，不把脚本、人设或用户反馈当作文件引用。"""
    if isinstance(value, str):
        if parse_companion_asset_path(value):
            return {value}
        try:
            return _paths(json.loads(value))
        except (ValueError, TypeError, RecursionError):
            return set()
    if isinstance(value, dict):
        return set().union(*(_paths(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_paths(item) for item in value))
    return set()


def action_asset_paths(action: CompanionAction) -> set[str]:
    return set().union(
        *(
            _paths(getattr(action, field))
            for field in (
                "artifact_path",
                "pose_path",
                "video_path",
                "cover_path",
                "hitmask_path",
                "result_json",
                "generation_state_json",
                "pose_generation_state_json",
                "accepted_asset_json",
            )
        ),
    )


def pack_asset_paths(pack: CompanionActionPack) -> set[str]:
    return set().union(
        *(_paths(value) for value in (pack.reference_path, pack.manifest_path, pack.cover_path, pack.context_json)),
    )


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
        reviews = (await db.scalars(select(CompanionMediaReview).where(CompanionMediaReview.user_id == user_id))).all()
        for review in reviews:
            if (
                review.status != "pending"
                and review.media_url
                and review.updated_at < utc_now() - REVIEW_ASSET_RETENTION
            ):
                await retire_action_assets(db, user_id, [review.media_url])
                review.media_url = ""
        await db.flush()
        referenced = {review.media_url for review in reviews if review.media_url}
        for outfit in await db.scalars(select(CompanionOutfit).where(CompanionOutfit.user_id == user_id)):
            referenced |= _paths(outfit.fullbody_url) | _paths(outfit.source_json)
        for avatar in await db.scalars(select(AvatarAsset).where(AvatarAsset.user_id == user_id)):
            referenced |= _paths(avatar.asset_url) | _paths(avatar.seed_fullbody_url)
        safe_to_collect = True
        for pack in await db.scalars(select(CompanionActionPack).where(CompanionActionPack.user_id == user_id)):
            referenced |= pack_asset_paths(pack)
            if pack.manifest_path:
                # 兼容已发布目录仍指向旧素材、动作 live 列却已清空的历史行。
                try:
                    parsed = parse_companion_asset_path(pack.manifest_path)
                    if parsed is None or parsed[0] != user_id:
                        raise ValueError("目录不属于当前用户")
                    manifest = Path(SETTINGS.data_dir) / pack.manifest_path
                    referenced |= _paths(json.loads(await asyncio.to_thread(manifest.read_text, encoding="utf-8")))
                except (OSError, ValueError, RecursionError):
                    logger.warning(
                        "action asset cleanup deferred: published catalog unreadable",
                        extra={"user_id": user_id, "pack_id": pack.id},
                        exc_info=True,
                    )
                    safe_to_collect = False
                    break
        if not safe_to_collect:
            return
        for action in await db.scalars(select(CompanionAction).where(CompanionAction.user_id == user_id)):
            referenced |= action_asset_paths(action)
        legacy = await asyncio.to_thread(_legacy_action_assets, user_id)
        orphans = legacy - referenced
        if orphans:
            await db.execute(
                insert(ActionAssetRetirement)
                .values([{"user_id": user_id, "path": path, "retired_at": utc_now()} for path in sorted(orphans)])
                .on_conflict_do_nothing(index_elements=[ActionAssetRetirement.user_id, ActionAssetRetirement.path]),
            )
        retired = (
            await db.scalars(
                select(ActionAssetRetirement).where(
                    ActionAssetRetirement.user_id == user_id,
                    ActionAssetRetirement.retired_at < utc_now() - ASSET_GRACE,
                ),
            )
        ).all()
        # 有引用的登记继续保留；最后一个引用释放时再次登记会重启宽限期。
        for entry in retired:
            if entry.path not in referenced:
                await asyncio.to_thread(unlink_companion_asset, entry.path)
                # 删除助手保留尽力清理语义；磁盘失败时不能丢掉下一轮所需的登记。
                if not (Path(SETTINGS.data_dir) / entry.path).exists():
                    await db.delete(entry)
        await db.commit()


async def delete_outfit_action_packs(db: AsyncSession, user_id: int, outfit_id: int) -> None:
    """调用方持外观锁；拒绝在用/制作中的包，其余关联包同事务删除并延迟回收。"""
    packs = (
        await db.scalars(
            select(CompanionActionPack).where(
                CompanionActionPack.user_id == user_id,
                CompanionActionPack.outfit_id == outfit_id,
            ),
        )
    ).all()
    if not packs:
        return
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
    for pack in packs:
        await db.delete(pack)
