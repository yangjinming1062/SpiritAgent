from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from .schemas_actions import PeekGeometry

# 动作媒体经 base64 JSON 通道上传，最大 32 MiB。
_VIDEO_CLIP_MAX_BYTES: int = 32 * 1024 * 1024


class _ActionMediaClip(BaseModel):
    model_config = ConfigDict(extra="forbid")

    media_type: Literal["image", "video"]
    action: str
    path: str
    sha256: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    peek_geometry: PeekGeometry | None = None
    content_rect: tuple[float, float, float, float] | None = None


class ImageClipSpec(_ActionMediaClip):
    """静态动作成品；命中遮罩独立落盘。"""

    media_type: Literal["image"] = "image"


class VideoClipSpec(_ActionMediaClip):
    """视频动作成品与实际计时；命中遮罩独立落盘。"""

    media_type: Literal["video"] = "video"
    frames: int = Field(gt=0)
    duration_ms: int = Field(gt=0)


class ActionResult(BaseModel):
    """生成、采纳与恢复共用的动作素材结果。"""

    model_config = ConfigDict(extra="forbid")

    clip: Annotated[ImageClipSpec | VideoClipSpec, Field(discriminator="media_type")]
    cover_path: str
    hitmask_path: str


class _ActionMediaUpload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    media_type: Literal["image", "video"]
    action: str = Field(min_length=1, max_length=32)
    data: str = Field(min_length=1, max_length=_VIDEO_CLIP_MAX_BYTES // 3 * 4 + 8)
    content_type: str | None = Field(default=None, max_length=64)


class ImageClipUpload(_ActionMediaUpload):
    """单张静态动作图片。"""

    media_type: Literal["image"]


class VideoClipUpload(_ActionMediaUpload):
    """动作视频及可选的源视频区间（秒）。"""

    media_type: Literal["video"]
    start_seconds: float | None = Field(default=None, ge=0)
    end_seconds: float | None = Field(default=None, ge=0)


ActionMediaUpload = Annotated[ImageClipUpload | VideoClipUpload, Field(discriminator="media_type")]


class VideoPackCreateRequest(BaseModel):
    """上传图片或视频创建动作包：外观 ID + 动作片段（至少包含必需动作）；画布缺省 512x768。"""

    model_config = ConfigDict(extra="forbid")

    outfit_id: int
    clips: list[ActionMediaUpload] = Field(min_length=1, max_length=12)
    canvas_width: int = Field(default=512, gt=0, le=1024)
    canvas_height: int = Field(default=768, gt=0, le=1024)


class VideoPackGenerateRequest(BaseModel):
    """按参考生成动作包：缺省取当前激活且已确认的外观；force 跳过同参考版本的包复用。
    source_pack_id + action 为单动作请求（补齐缺失动作或重做已有动作）。"""

    model_config = ConfigDict(extra="forbid")

    outfit_id: int | None = None
    force: bool = False
    source_pack_id: int | None = None
    action: Literal["idle", "walk_left", "walk_right", "drag", "peek_left", "peek_right"] | None = None
    feedback: str = Field(default="", max_length=1000)


class VideoPackEnsureSystemActionRequest(BaseModel):
    """按需补齐探身动作。"""

    model_config = ConfigDict(extra="forbid")

    action: Literal["peek_left", "peek_right"]


class ActionMediaResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    media_type: Literal["image", "video"]
    action: str
    # 动态动作的用户可见名称；系统动作为空，界面按槽位本地化。
    name: str = ""
    status: str
    stage: str
    error: str | None = None
    media_url: str | None = None
    feedback: str = ""
    peek_geometry: PeekGeometry | None = None
    content_rect: tuple[float, float, float, float] | None = None


class VideoActionImageResponse(ActionMediaResponse):
    media_type: Literal["image"] = "image"


class VideoActionVideoResponse(ActionMediaResponse):
    media_type: Literal["video"] = "video"
    motion_prompt: str = ""


VideoActionResponse = Annotated[
    VideoActionImageResponse | VideoActionVideoResponse,
    Field(discriminator="media_type"),
]


class VideoPackResponse(BaseModel):
    id: int
    outfit_id: int | None = None
    pack_version: int
    status: str
    active: bool = False
    identity_review: Literal["none", "pass", "review", "accepted"] = "none"
    identity_review_reason: str = ""
    content_hash: str | None = None
    manifest_url: str | None = None
    can_retry: bool = False
    can_regenerate: bool = False
    actions: list[VideoActionResponse] = Field(default_factory=list)
    error: str | None = None


class VideoPackListResponse(BaseModel):
    packs: list[VideoPackResponse]
