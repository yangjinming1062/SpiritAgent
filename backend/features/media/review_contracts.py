"""媒体复核的公开归属与响应契约。"""

from typing import Literal

from pydantic import BaseModel


class MediaReviewPublication(BaseModel):
    """复核项落库的所属动作，采纳与拒绝时据此定位；title 为动态动作的名称，系统动作无名称、以 system_slot 标识。"""

    kind: Literal["action"]
    pack_id: int
    action_id: int
    title: str
    system_slot: str = ""


class MediaReviewResponse(BaseModel):
    id: int
    status: Literal["pending", "accepted", "rejected"]
    reason: str = ""
    media_type: Literal["image", "video"]
    media_url: str
    # 动态动作的名称；系统动作无名称，只带槽位键，界面按槽位本地化。
    title: str = ""
    system_slot: str = ""
