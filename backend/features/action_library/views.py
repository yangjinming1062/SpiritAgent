"""动作素材的名称规范化与对外投影；查询与写入共用，不反向依赖业务服务。"""

import unicodedata

from .models import ActionDefinition
from .schemas import ActionAssetView


def normalize_action_name(name: str) -> str:
    return unicodedata.normalize("NFKC", name).strip().casefold()


def definition_view(row: ActionDefinition) -> ActionAssetView:
    return ActionAssetView(
        id=row.id,
        name=row.name,
        motion_description=row.motion_description,
        use_when=row.use_when_json,
        avoid_when=row.avoid_when_json,
        allowed_triggers=row.allowed_triggers_json,
        clip_kind=row.clip_kind,  # type: ignore[arg-type]
        duration_seconds=row.duration_seconds,
        semantic_tags=row.semantic_tags_json,
        performance_role=row.performance_role,  # type: ignore[arg-type]
        motion_intensity=row.motion_intensity,  # type: ignore[arg-type]
        interruptible=row.interruptible,
        reason=row.reason,
        source=row.source,  # type: ignore[arg-type]
        preset_key=row.preset_key,
        status=row.status,  # type: ignore[arg-type]
        review_reason=row.review_reason,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
