"""已采纳动作素材与制作尝试的边界；目录及播放只消费已采纳版本。"""

from modules.companion import CompanionAction
from pydantic import BaseModel, ConfigDict, Field


class AcceptedActionAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result_json: str
    video_path: str
    video_hash: str
    actual_duration_ms: int = Field(gt=0)
    frames: int = Field(gt=0)
    metadata_revision: int = Field(gt=0)
    kind: str
    loopable: bool
    cover_path: str | None = None
    hitmask_path: str | None = None
    hitmask_grid_w: int | None = None
    hitmask_grid_h: int | None = None
    hitmask_fps: int | None = None
    peek_geometry_json: str | None = None
    content_rect_json: str | None = None

    def paths(self) -> set[str]:
        return {path for path in (self.video_path, self.cover_path, self.hitmask_path) if path}


def accepted_action_asset(action: CompanionAction) -> AcceptedActionAsset | None:
    if action.accepted_asset_json:
        return AcceptedActionAsset.model_validate_json(action.accepted_asset_json)
    if action.status != "succeeded" or not action.result_json or not action.video_path:
        return None
    return _current_asset(action)


def _current_asset(action: CompanionAction) -> AcceptedActionAsset:
    values = {field: getattr(action, field) for field in AcceptedActionAsset.model_fields}
    # 旧备份可能只保存设计时长，沿原发布兜底读取，不补造新的制作成品。
    values["actual_duration_ms"] = action.actual_duration_ms or int((action.target_duration_seconds or 2) * 1000)
    values["frames"] = action.frames or int((action.target_duration_seconds or 2) * 24)
    return AcceptedActionAsset(**values)


def accept_action_asset(action: CompanionAction) -> None:
    action.accepted_asset_json = _current_asset(action).model_dump_json()


def preserve_action_asset(action: CompanionAction) -> None:
    if not action.accepted_asset_json and action.status == "succeeded" and action.result_json and action.video_path:
        accept_action_asset(action)
