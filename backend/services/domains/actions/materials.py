"""已采纳动作素材与制作尝试的边界；目录及播放只消费已采纳版本。"""

from typing import Annotated, Literal, Self

from modules.companion import ActionResult, CompanionAction
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator


class _AcceptedActionAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    media_type: Literal["image", "video"]
    result_json: str
    media_path: str
    media_hash: str
    metadata_revision: int = Field(gt=0)
    cover_path: str | None = None
    hitmask_path: str | None = None
    hitmask_grid_w: int | None = None
    hitmask_grid_h: int | None = None
    peek_geometry_json: str | None = None
    content_rect_json: str | None = None

    def parse_result(self) -> ActionResult:
        return ActionResult.model_validate_json(self.result_json)

    @model_validator(mode="after")
    def validate_result_media(self) -> Self:
        result = self.parse_result()
        clip = result.clip
        if clip.media_type != self.media_type:
            raise ValueError("动作素材类型与处理结果不一致")
        if clip.path != self.media_path or clip.sha256 != self.media_hash:
            raise ValueError("动作素材引用与处理结果不一致")
        for field in ("cover_path", "hitmask_path"):
            path = getattr(self, field)
            if path is not None and path != getattr(result, field):
                raise ValueError("动作配套资产与处理结果不一致")
        if (
            isinstance(self, AcceptedVideoActionAsset)
            and clip.media_type == "video"
            and (clip.duration_ms != self.actual_duration_ms or clip.frames != self.frames)
        ):
            raise ValueError("动作视频计时与处理结果不一致")
        return self

    def paths(self) -> set[str]:
        result = self.parse_result()
        return {
            path
            for path in (self.media_path, self.cover_path, self.hitmask_path, result.cover_path, result.hitmask_path)
            if path
        }


class AcceptedImageActionAsset(_AcceptedActionAsset):
    media_type: Literal["image"] = "image"


class AcceptedVideoActionAsset(_AcceptedActionAsset):
    media_type: Literal["video"] = "video"
    actual_duration_ms: int = Field(gt=0)
    frames: int = Field(gt=0)
    kind: Literal["once", "loop"]
    loopable: bool
    hitmask_fps: int = Field(gt=0, le=60)


AcceptedActionAsset = Annotated[
    AcceptedImageActionAsset | AcceptedVideoActionAsset,
    Field(discriminator="media_type"),
]
_ACCEPTED_ASSET_ADAPTER = TypeAdapter(AcceptedActionAsset)


def parse_accepted_action_asset_json(value: str) -> AcceptedActionAsset:
    return _ACCEPTED_ASSET_ADAPTER.validate_json(value)


def accepted_action_asset(action: CompanionAction) -> AcceptedActionAsset | None:
    if action.accepted_asset_json:
        return parse_accepted_action_asset_json(action.accepted_asset_json)
    if action.status != "succeeded" or not action.result_json or not action.media_path:
        return None
    return _current_asset(action)


def _current_asset(action: CompanionAction) -> AcceptedActionAsset:
    model = AcceptedImageActionAsset if action.media_type == "image" else AcceptedVideoActionAsset
    values = {field: getattr(action, field) for field in model.model_fields}
    return model.model_validate(values)


def accept_action_asset(action: CompanionAction) -> None:
    action.accepted_asset_json = _current_asset(action).model_dump_json()


def preserve_action_asset(action: CompanionAction) -> None:
    if not action.accepted_asset_json and action.status == "succeeded" and action.result_json and action.media_path:
        accept_action_asset(action)
