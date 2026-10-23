"""动作素材的名称规范化与对外投影；查询与写入共用，不反向依赖业务服务。"""

import json
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
        use_when=load_string_list(row.use_when_json),
        avoid_when=load_string_list(row.avoid_when_json),
        allowed_triggers=load_string_list(row.allowed_triggers_json),
        clip_kind=row.clip_kind,  # type: ignore[arg-type]
        duration_seconds=row.duration_seconds,
        reason=row.reason,
        source=row.source,  # type: ignore[arg-type]
        status=row.status,  # type: ignore[arg-type]
        review_reason=row.review_reason,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def load_string_list(raw: str) -> list[str]:
    try:
        value = json.loads(raw or "[]")
    except ValueError:
        return []
    return [str(item) for item in value] if isinstance(value, list) else []


def dump_string_list(values: list[str]) -> str:
    return json.dumps([str(item) for item in values], ensure_ascii=False)
