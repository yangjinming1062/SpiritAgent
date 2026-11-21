"""影像导入修改关联前的共享素材引用检查。"""

from typing import Literal

from sqlalchemy import and_, column, func, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from .models import VisualImportJob, VisualItem, VisualJob, VisualPlayback, VisualPresentation, VisualVersion

MaterialKind = Literal["outfit", "scene", "action"]

_MATERIAL_COLUMNS = {"outfit": "outfit_id", "scene": "scene_id", "action": "action_id"}
_MATERIAL_JSON_KEYS = {"outfit": "material_id", "scene": "scene_id", "action": "action_id"}


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
        # 即使正在修改同一份导入影像，待播指令也可能仍要认领它的原版本。
        pending_play = (
            select(VisualPlayback.id)
            .join(VisualPresentation, VisualPresentation.user_id == VisualPlayback.user_id)
            .where(
                VisualPlayback.user_id == user_id,
                VisualPlayback.version_id == VisualVersion.id,
                VisualPlayback.play_id == VisualPresentation.pending_command_json["play_id"].astext,
                VisualPlayback.presentation_epoch == VisualPresentation.presentation_epoch,
                VisualPlayback.state_revision == VisualPresentation.state_revision,
                VisualPlayback.status.in_(("queued", "claimed")),
                VisualPlayback.expires_at > func.now(),
            )
            .exists()
        )
        version_query = version_query.where(or_(VisualVersion.visual_id != exclude_visual_id, pending_play))
        job_query = job_query.where(
            and_(
                (VisualJob.target_visual_id.is_(None) | (VisualJob.target_visual_id != exclude_visual_id)),
                (VisualJob.result_visual_id.is_(None) | (VisualJob.result_visual_id != exclude_visual_id)),
                (VisualJob.result_image_id.is_(None) | (VisualJob.result_image_id != exclude_visual_id)),
            )
        )
    for query in (visual_query, version_query, job_query):
        if await db.scalar(query.limit(1).execution_options(discover_private_works=True)) is not None:
            return True

    # 播放快照冻结成品的素材 ID；静态舞台和基础组合各自持有素材引用。
    json_id = str(material_id)
    ref_key = _MATERIAL_JSON_KEYS[kind]
    on_stage = [
        VisualPresentation.current_snapshot_json[column_name].astext == json_id,
        VisualPresentation.loop_snapshot_json[column_name].astext == json_id,
    ]
    if kind in {"outfit", "scene"}:
        on_stage.extend(
            (
                VisualPresentation.pending_static_json[kind][ref_key].astext == json_id,
                VisualPresentation.current_static_json[kind][ref_key].astext == json_id,
                VisualPresentation.composition_json[f"{kind}_material"][ref_key].astext == json_id,
            )
        )
    if await db.scalar(select(VisualPresentation.user_id).where(VisualPresentation.user_id == user_id, or_(*on_stage))):
        return True

    # 用户选用已有素材后，即使同一导入任务的另一条目还在排队，也要保留所选素材。
    components = func.jsonb_array_elements(VisualImportJob.result_json["components"]).table_valued(
        column("value", JSONB)
    )
    item = components.c.value
    operations = func.jsonb_array_elements(VisualImportJob.selections_json).table_valued(column("value", JSONB))
    operation = operations.c.value
    selected_keys = (
        select(operation["key"].astext)
        .select_from(operations)
        .where(
            operation.contains({"command": "existing", "material_id": json_id}),
            operation["state"].astext.in_(("queued", "running", "paused")),
        )
        .correlate(VisualImportJob.__table__)
    )
    selected = and_(item["material_id"].astext == json_id, item["status"].astext.not_in(("undone", "skipped")))
    frozen_source = VisualImportJob.context_json["pending_application"]["snapshot"]["source"]
    pending_publication = frozen_source[kind][ref_key].astext == json_id
    if exclude_import_job_id is not None:
        other_job = VisualImportJob.id != exclude_import_job_id
        selected = and_(other_job, selected)
        pending_publication = and_(other_job, pending_publication)
    linked_component = (
        select(1)
        .select_from(components)
        .where(
            item["observation"]["kind"].astext == kind,
            or_(selected, item["observation"]["key"].astext.in_(selected_keys)),
        )
        .correlate(VisualImportJob.__table__)
        .exists()
    )
    import_query = select(VisualImportJob.id).where(
        VisualImportJob.user_id == user_id,
        or_(linked_component, pending_publication),
    )
    return await db.scalar(import_query.limit(1).execution_options(discover_private_works=True)) is not None
