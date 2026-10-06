"""正式资产的业务引用集合；在线回收与备份恢复规划共用。"""

import asyncio
import json
import re
from datetime import timedelta
from pathlib import Path

from components import utc_now
from modules.channels import ChannelBinding, ChannelDelivery
from modules.companion import (
    ActionAssetRetirement,
    AvatarAsset,
    CompanionAction,
    CompanionActionPack,
    CompanionCharacterCard,
    CompanionMediaReview,
    CompanionOutfit,
    CompanionPost,
    CompanionScene,
    FullbodyCandidate,
    PostPublication,
)
from modules.conversation import Conversation, Message
from modules.media import VideoGenJob
from modules.ws import WSEvent
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.infrastructure.assets import (
    normalize_asset_reference,
    parse_companion_asset_path,
    resolve_companion_asset_path,
)

# 标点和 Markdown 边界结束引用；不能把任意文件名子串当作本账户资产。
_ASSET_TOKEN = re.compile(
    r"(?<![\w/-])(?:https?://[^\s<>\"'()\[\]{}]+)?(?:/api/companion/asset/|companion-assets/)[^\s<>\"'()\[\]{},;]+",
)
MESSAGE_ASSET_FIELDS = ("content", "tool_calls", "media_json", "reply_json")
ACTION_ASSET_FIELDS = (
    "artifact_path",
    "pose_path",
    "media_path",
    "cover_path",
    "hitmask_path",
    "result_json",
    "generation_state_json",
    "pose_generation_state_json",
    "accepted_asset_json",
)
PACK_ASSET_FIELDS = ("reference_path", "manifest_path", "cover_path", "context_json")


class AssetReferencesUnavailable(RuntimeError):
    """引用目录不可读时阻止整个账户的回收，不能将未知当作无引用。"""


def asset_paths(value: object, user_id: int) -> set[str]:
    if isinstance(value, dict):
        return set().union(*(asset_paths(item, user_id) for item in value.values()))
    if isinstance(value, (list, tuple)):
        return set().union(*(asset_paths(item, user_id) for item in value))
    if not isinstance(value, str):
        return set()
    result: set[str] = set()
    candidates = [value, *(match.group(0).rstrip(".!?，。；！") for match in _ASSET_TOKEN.finditer(value))]
    for candidate in candidates:
        path = normalize_asset_reference(candidate)
        parsed = parse_companion_asset_path(path)
        if parsed is not None and parsed[0] == user_id:
            result.add(path)
    if value.lstrip().startswith(("{", "[", '"')):
        try:
            decoded = json.loads(value)
        except (ValueError, RecursionError):
            pass
        else:
            if decoded != value:
                result |= asset_paths(decoded, user_id)
    return result


def message_asset_paths(message: Message, user_id: int) -> set[str]:
    return asset_paths([getattr(message, field) for field in MESSAGE_ASSET_FIELDS], user_id)


async def _catalog_asset_paths(storage_path: str, user_id: int) -> set[str]:
    parsed = parse_companion_asset_path(storage_path)
    try:
        if parsed is None or parsed[0] != user_id:
            raise ValueError("Action catalog path escapes its account")
        resolved = resolve_companion_asset_path(*parsed)
        if resolved is None:
            raise ValueError("Action catalog is missing or unsafe")
        path, _ = resolved
        manifest = json.loads(await asyncio.to_thread(path.read_text, encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as exc:
        raise AssetReferencesUnavailable(f"Action catalog unavailable: {storage_path}") from exc
    return asset_paths(manifest, user_id)


async def collect_live_asset_paths(db: AsyncSession, user_id: int) -> set[str]:
    """只把可见业务、可恢复任务及待投递事件作为引用，不以历史诊断行续命文件。"""
    referenced: set[str] = set()
    for message in await db.scalars(select(Message).join(Conversation).where(Conversation.user_id == user_id)):
        referenced |= message_asset_paths(message, user_id)
    fields_by_model = (
        (AvatarAsset, ("asset_url", "seed_fullbody_url")),
        (CompanionCharacterCard, ("portrait_source_path", "body_source_path")),
        (CompanionOutfit, ("fullbody_url", "source_json")),
        (CompanionAction, ACTION_ASSET_FIELDS),
        (CompanionActionPack, PACK_ASSET_FIELDS),
        (CompanionScene, ("media_path", "generation_state_json", "regeneration_state_json", "reference_image")),
        (CompanionPost, ("body", "media_url", "audio_url", "context_json")),
    )
    for model, fields in fields_by_model:
        for row in await db.scalars(select(model).where(model.user_id == user_id)):
            referenced |= asset_paths([getattr(row, field, None) for field in fields], user_id)
            if isinstance(row, CompanionActionPack) and row.manifest_path:
                referenced |= await _catalog_asset_paths(row.manifest_path, user_id)

    for candidate in await db.scalars(
        select(FullbodyCandidate).where(
            FullbodyCandidate.user_id == user_id,
            FullbodyCandidate.status.in_(("pending", "ready", "failed", "analyzing")),
        ),
    ):
        referenced |= asset_paths([candidate.image_url, candidate.base_fullbody_url], user_id)
    for review in await db.scalars(
        select(CompanionMediaReview).where(
            CompanionMediaReview.user_id == user_id,
            (CompanionMediaReview.status == "pending")
            | (CompanionMediaReview.updated_at >= utc_now() - timedelta(days=7)),
        ),
    ):
        referenced |= asset_paths([review.media_url, review.publication], user_id)
    for publication in await db.scalars(
        select(PostPublication).where(
            PostPublication.user_id == user_id,
            PostPublication.status.in_(("queued", "running", "result_unknown")),
        ),
    ):
        referenced |= asset_paths([publication.request_json, publication.plan_json, publication.progress_json], user_id)
    for job in await db.scalars(
        select(VideoGenJob).where(
            VideoGenJob.user_id == user_id,
            VideoGenJob.cleanup_requested_at.is_(None),
            VideoGenJob.status.not_in(("discarded", "cancelled", "failed")),
        ),
    ):
        # 完成的聊天资源由消息/独立资产持有，作业诊断本身不是永久引用。
        if job.status == "succeeded":
            continue
        referenced |= asset_paths(
            [job.video_url, job.candidate_video_url, job.params_json, job.generation_state_json],
            user_id,
        )
    for delivery in await db.scalars(
        select(ChannelDelivery)
        .join(ChannelBinding)
        .where(
            ChannelBinding.user_id == user_id,
            ChannelDelivery.status == "pending",
        ),
    ):
        referenced |= asset_paths(delivery.payload_json, user_id)
    for retirement in await db.scalars(
        select(ActionAssetRetirement).where(
            ActionAssetRetirement.user_id == user_id,
            ActionAssetRetirement.retired_at > utc_now() - timedelta(hours=24),
        ),
    ):
        referenced |= asset_paths(retirement.path, user_id)
        if Path(retirement.path).name.startswith("action-catalog-") and retirement.path.endswith(".json"):
            referenced |= await _catalog_asset_paths(retirement.path, user_id)
    for event in await db.scalars(
        select(WSEvent).where(WSEvent.user_id == user_id, WSEvent.status.in_(("PENDING", "PROCESSING"))),
    ):
        referenced |= asset_paths(event.payload, user_id)
    return referenced
