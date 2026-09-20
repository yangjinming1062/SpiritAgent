from pydantic import BaseModel, ConfigDict, Field

# 视频片段较大（透明 WebM 数秒），经同一 base64 JSON 通道放宽到 32 MiB；
# 真正的分片上传通道接入后在此收紧。
_VIDEO_CLIP_MAX_BYTES: int = 32 * 1024 * 1024


class VideoClipUpload(BaseModel):
    """单个动作的源片段：动作键 + 片段字节（base64）+ 可选长视频区间（秒）。"""

    model_config = ConfigDict(extra="forbid")

    action: str = Field(min_length=1, max_length=32)
    data: str = Field(min_length=1, max_length=_VIDEO_CLIP_MAX_BYTES // 3 * 4 + 8)
    content_type: str | None = Field(default=None, max_length=64)
    start_seconds: float | None = Field(default=None, ge=0)
    end_seconds: float | None = Field(default=None, ge=0)


class VideoPackCreateRequest(BaseModel):
    """上传片段创建视频包：外观 ID + 必需动作片段；画布缺省 512x768。"""

    model_config = ConfigDict(extra="forbid")

    outfit_id: int
    clips: list[VideoClipUpload] = Field(min_length=1, max_length=12)
    canvas_width: int = Field(default=512, gt=0, le=1024)
    canvas_height: int = Field(default=768, gt=0, le=1024)


class VideoPackGenerateRequest(BaseModel):
    """按参考生成视频包：缺省取当前激活且已确认的外观；force 跳过同参考版本的包复用。"""

    model_config = ConfigDict(extra="forbid")

    outfit_id: int | None = None
    force: bool = False


class VideoPackResponse(BaseModel):
    id: int
    outfit_id: int | None = None
    pack_version: int
    status: str
    active: bool = False
    content_hash: str | None = None
    manifest_url: str | None = None
    error: str | None = None


class VideoPackListResponse(BaseModel):
    packs: list[VideoPackResponse]
