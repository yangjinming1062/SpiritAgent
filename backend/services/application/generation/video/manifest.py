"""角色视频动作包描述符（manifest）：`spiritagent.video.pack/1`。

manifest 只含数据：不可变资源路径 + 内容哈希、画布与脚底锚点、动作映射、
运动与调度约束。不允许脚本、任意远程 URL 或处理参数；资源路径必须是
`companion-assets/<uid>/<file>` 形态的裸存储路径，签名在读取时生成。
"""

import re
from typing import Literal

from modules.companion import REQUIRED_VIDEO_ACTIONS
from pydantic import BaseModel, ConfigDict, Field

from services.infrastructure.video_processing import (
    MAX_CANVAS_HEIGHT,
    MAX_CANVAS_WIDTH,
)

MANIFEST_SCHEMA = "spiritagent.video.pack/1"
DEFAULT_ACTION: str = "idle"

_ACTION_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_STORAGE_PATH_RE = re.compile(r"^companion-assets/\d+/[A-Za-z0-9._-]+$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class VideoClipSpec(BaseModel):
    """单个动作片段：不可变资源 + 计时元数据 + 循环与接点标记。"""

    model_config = ConfigDict(extra="forbid")

    action: str
    path: str
    sha256: str
    bytes: int = Field(gt=0)
    frames: int = Field(gt=0)
    duration_ms: int = Field(gt=0)
    loop: bool = True
    # 进入 / 退出姿势标记（可选）：客户端用于判断接点是否需要过渡，不做形变。
    enter_pose: str | None = None
    exit_pose: str | None = None
    # 逐帧低分辨率 alpha 命中遮罩 [frame][row 列位行]。
    hitmask: list[list[int]] = Field(default_factory=list)
    hitmask_grid: tuple[int, int] | None = None
    hitmask_fps: int = Field(gt=0, le=60)


class VideoPackCanvas(BaseModel):
    """统一画布与锚点：全包片段共享；脚底锚点为画布底边中点。"""

    model_config = ConfigDict(extra="forbid")

    width: int = Field(gt=0, le=MAX_CANVAS_WIDTH)
    height: int = Field(gt=0, le=MAX_CANVAS_HEIGHT)
    fps: int = Field(gt=0, le=60)


class VideoPackManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["spiritagent.video.pack/1"]
    pack_id: int
    character_id: int | None = None
    appearance_id: int | None = None
    pack_version: int = Field(gt=0)
    canvas: VideoPackCanvas
    clips: list[VideoClipSpec] = Field(min_length=1)
    # 默认封面（webp，保留 alpha）；封面不标记为可播放视频。
    cover_path: str | None = None
    default_action: str = DEFAULT_ACTION
    # 基准行走速度（画布宽/秒）；客户端按显示缩放换算容器位移并钳制倍率。
    walk_speed: float | None = Field(default=None, gt=0)
    max_playback_rate: float = Field(default=1.25, gt=0, le=2.0)
    # 左右行走是否允许水平镜像复用；仅在资产明确声明时开启。
    mirror_allowed: bool = False
    min_client_version: str = "0.0.0"


class ManifestValidationError(ValueError):
    """manifest 未通过发布校验；str 为公开文案。"""


def validate_pack_manifest(manifest: VideoPackManifest) -> None:
    """发布门禁：必需动作齐备、动作唯一、资源路径与哈希形态合法、默认动作在列。
    视觉质量（姿态、边缘、身份一致性）不由本校验背书，验收归预览确认。"""
    if manifest.schema_version != MANIFEST_SCHEMA:
        raise ManifestValidationError("manifest schema 版本不受支持")
    if manifest.canvas.width <= 0 or manifest.canvas.height <= 0:
        raise ManifestValidationError("画布尺寸无效")

    seen: set[str] = set()
    for clip in manifest.clips:
        if not _ACTION_KEY_RE.match(clip.action):
            raise ManifestValidationError(f"动作键不合法：{clip.action}")
        if clip.action in seen:
            raise ManifestValidationError(f"动作重复：{clip.action}")
        seen.add(clip.action)
        if not _STORAGE_PATH_RE.match(clip.path):
            raise ManifestValidationError(f"资源路径不合法：{clip.action}")
        if not _SHA256_RE.match(clip.sha256):
            raise ManifestValidationError(f"资源哈希缺失：{clip.action}")
        if clip.action == manifest.default_action and not clip.loop:
            raise ManifestValidationError("默认动作必须可循环")

    missing = [action for action in REQUIRED_VIDEO_ACTIONS if action not in seen]
    if missing:
        raise ManifestValidationError("缺少必需动作：" + "、".join(missing))
    if manifest.default_action not in seen:
        raise ManifestValidationError("默认动作不在动作列表中")
