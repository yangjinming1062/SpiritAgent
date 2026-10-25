"""资产回收消费业务模块声明的引用，不访问其持久模型。"""

from typing import Any, Protocol

from infrastructure.assets.references import asset_paths
from sqlalchemy.ext.asyncio import AsyncSession

from features.assets.contributions import asset_contributions


class MessageAssetSnapshot(Protocol):
    content: str | None
    content_type: str
    role: str
    tool_calls: list[dict[str, Any]] | None
    media_json: list[dict[str, Any]] | None
    reply_json: dict[str, Any] | None


MESSAGE_ASSET_FIELDS = ("content", "tool_calls", "media_json", "reply_json")


class AssetReferencesUnavailable(RuntimeError):
    """引用目录不可读时阻止整个账户的回收，不能将未知当作无引用。"""


def message_asset_paths(message: MessageAssetSnapshot, user_id: int) -> set[str]:
    fields = (
        MESSAGE_ASSET_FIELDS
        if message.content_type != "text" or message.role == "tool"
        else ("tool_calls", "media_json", "reply_json")
    )
    return asset_paths([getattr(message, field) for field in fields], user_id)


async def collect_live_asset_paths(db: AsyncSession, user_id: int) -> set[str]:
    referenced: set[str] = set()
    for contribution in asset_contributions():
        referenced |= await contribution.references(db, user_id)
    return referenced
