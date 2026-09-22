"""动作素材处理中间结构：片段规格与画布。

可播清单由 domains/actions/publishing 发布的动作目录
（`spiritagent.action.pack`）维护。
"""

from pydantic import BaseModel, ConfigDict, Field

from services.infrastructure.video_processing import (
    MAX_CANVAS_HEIGHT,
    MAX_CANVAS_WIDTH,
)


class VideoClipSpec(BaseModel):
    """单个动作片段的处理结果：不可变资源 + 计时元数据 + 循环与接点标记。"""

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
    # 逐帧低分辨率 alpha 命中遮罩 [frame][row 列位行]；目录引用独立 hitmask 资源。
    hitmask: list[list[int]] = Field(default_factory=list)
    hitmask_grid: tuple[int, int] | None = None
    hitmask_fps: int = Field(gt=0, le=60)


class VideoPackCanvas(BaseModel):
    """统一画布与锚点：全包片段共享；脚底锚点为画布底边中点。"""

    model_config = ConfigDict(extra="forbid")

    width: int = Field(gt=0, le=MAX_CANVAS_WIDTH)
    height: int = Field(gt=0, le=MAX_CANVAS_HEIGHT)
    fps: int = Field(gt=0, le=60)
