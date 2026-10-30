"""影像导入修改关联前的共享素材引用检查。"""

from typing import Literal

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import VisualImportJob, VisualItem, VisualJob, VisualPresentation, VisualVersion

MaterialKind = Literal["outfit", "scene", "action"]

_MATERIAL_COLUMNS = {
    "outfit": "outfit_id",
    "scene": "scene_id",
    "action": "action_id",
}
_MATERIAL_JSON_KEYS = {
    "outfit": "material_id",
    "scene": "scene_id",
    "action": "action_id",
}


def _analysis_contains_material(value: object, kind: MaterialKind, material_id: int) -> bool:
    """只把分析结果中的当前关联算作引用，候选列表不是正式使用。"""

    if not isinstance(value, dict):
        return False
    source_key = _MATERIAL_JSON_KEYS[kind]
    reference_names = {
        "outfit": ("outfit", "outfit_material"),
        "scene": ("scene", "scene_material"),
        "action": ("action",),
    }[kind]
    for source_name in ("source", ""):
        source = value if not source_name else value.get(source_name)
        if not isinstance(source, dict):
            continue
        reference = next((source.get(name) for name in reference_names if source.get(name) is not None), None)
        if isinstance(reference, dict) and reference.get(source_key) == material_id:
            return True
    snapshot = value.get("snapshot_json")
    if isinstance(snapshot, dict) and _analysis_contains_material(snapshot, kind, material_id):
        return True
    components = value.get("components")
    if isinstance(components, list):
        for component in components:
            if not isinstance(component, dict):
                continue
            observation = component.get("observation")
            if (
                isinstance(observation, dict)
                and observation.get("kind") == kind
                and component.get("material_id") == material_id
                and component.get("status") not in {"undone", "skipped"}
            ):
                return True
    # context_json.history 只保存来源轨迹，不代表当前仍在使用该素材。
    return False


async def material_has_external_references(
    db: AsyncSession,
    user_id: int,
    kind: MaterialKind,
    material_id: int,
    *,
    exclude_import_job_id: int | None = None,
    exclude_visual_id: int | None = None,
) -> bool:
    """判断素材是否仍被其它正式内容或分析结果使用。

    ``exclude_visual_id`` 用于同一份导入影像修改关联时忽略它自己的旧版本；旧版本
    仍保留冻结描述，但不应阻止这次导入清理自己创建且已不再使用的素材。
    """

    column_name = _MATERIAL_COLUMNS[kind]
    visual_column = getattr(VisualItem, column_name)
    version_column = getattr(VisualVersion, column_name)
    job_column = getattr(VisualJob, column_name)

    visual_query = select(VisualItem.id).where(VisualItem.user_id == user_id, visual_column == material_id)
    version_query = select(VisualVersion.id).where(VisualVersion.user_id == user_id, version_column == material_id)
    job_query = select(VisualJob.id).where(VisualJob.user_id == user_id, job_column == material_id)
    if exclude_visual_id is not None:
        visual_query = visual_query.where(VisualItem.id != exclude_visual_id)
        version_query = version_query.where(VisualVersion.visual_id != exclude_visual_id)
        job_query = job_query.where(
            and_(
                (VisualJob.target_visual_id.is_(None) | (VisualJob.target_visual_id != exclude_visual_id)),
                (VisualJob.result_visual_id.is_(None) | (VisualJob.result_visual_id != exclude_visual_id)),
                (VisualJob.result_image_id.is_(None) | (VisualJob.result_image_id != exclude_visual_id)),
            )
        )
    if await db.scalar(visual_query.limit(1)) is not None:
        return True
    if await db.scalar(version_query.limit(1)) is not None:
        return True
    if await db.scalar(job_query.limit(1)) is not None:
        return True

    # 静态呈现可能只保存素材冻结引用而没有成品列，不能因撤销动作破坏当前舞台。
    presentations = await db.execute(
        select(
            VisualPresentation.pending_static_json,
            VisualPresentation.current_static_json,
            VisualPresentation.composition_json,
            VisualPresentation.current_snapshot_json,
            VisualPresentation.loop_snapshot_json,
        ).where(VisualPresentation.user_id == user_id)
    )
    for row in presentations:
        if any(_analysis_contains_material(value, kind, material_id) for value in row):
            return True

    import_query = select(
        VisualImportJob.id,
        VisualImportJob.result_json,
        VisualImportJob.context_json,
    ).where(VisualImportJob.user_id == user_id)
    if exclude_import_job_id is not None:
        import_query = import_query.where(VisualImportJob.id != exclude_import_job_id)
    for row in await db.execute(import_query):
        if _analysis_contains_material(row.result_json, kind, material_id) or _analysis_contains_material(
            row.context_json, kind, material_id
        ):
            return True
    return False
