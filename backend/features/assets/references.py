"""资产回收消费业务模块声明的引用，不访问其持久模型。"""

from typing import Protocol

from infrastructure.assets.references import asset_paths
from sqlalchemy.ext.asyncio import AsyncSession

from features.assets.contributions import asset_contributions


class MessageAssetSnapshot(Protocol):
    content: str
    tool_calls: str | None
    media_json: str
    reply_json: str


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


def message_asset_paths(message: MessageAssetSnapshot, user_id: int) -> set[str]:
    return asset_paths([getattr(message, field) for field in MESSAGE_ASSET_FIELDS], user_id)


async def collect_live_asset_paths(db: AsyncSession, user_id: int) -> set[str]:
    referenced: set[str] = set()
    for contribution in asset_contributions():
        referenced |= await contribution.references(db, user_id)
    return referenced
