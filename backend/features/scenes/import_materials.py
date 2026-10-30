"""将已检查的原件像素作为环境素材采纳，不补画遮挡或启动生图。"""

from infrastructure.assets.asset_store import resolve_asset_reference
from infrastructure.domain_locks import lock_domain
from infrastructure.text import utc_now
from PIL import Image
from runtime.events.emit import emit_ws_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.visuals.static_presentation import invalidate_scene_presentation

from .asset_references import sync_scene_assets
from .models import CompanionScene
from .queries import scene_view
from .query_contracts import SceneSnapshot


async def accept_imported_scene(
    db: AsyncSession,
    user_id: int,
    *,
    source_key: str,
    path: str,
    name: str,
    description: str,
) -> SceneSnapshot:
    await lock_domain(db, user_id, "scenes")
    existing = await db.scalar(
        select(CompanionScene).where(CompanionScene.user_id == user_id, CompanionScene.source_key == source_key)
    )
    if existing is not None:
        return scene_view(existing)
    resolved = resolve_asset_reference(path)
    if resolved is None:
        raise ValueError("提取的环境图片缺失")
    with Image.open(resolved[0]) as image:
        size = {"width": image.width, "height": image.height}
    row = CompanionScene(
        user_id=user_id,
        source_key=source_key,
        status="ready",
        origin="user_request",
        source="user_upload",
        title=name[:80],
        description=description,
        media_path=path,
        upload_source_path=path,
        target_size_json=size,
        source_size_json=size,
        image_size_json=size,
        ready_at=utc_now(),
    )
    row.initial_job.stage = "complete"
    db.add(row)
    await db.flush()
    await sync_scene_assets(db, row)
    emit_ws_event(db, user_id=user_id, event_type="companion.scene.updated", payload={"scene_id": row.id})
    return scene_view(row)


async def undo_imported_scene(db: AsyncSession, user_id: int, material_id: int, source_key: str) -> None:
    row = await db.scalar(
        select(CompanionScene).where(CompanionScene.user_id == user_id, CompanionScene.id == material_id)
    )
    if row is None:
        return
    if row.source_key != source_key:
        raise ValueError("不能撤销并非由这次影像提取创建的环境")
    await lock_domain(db, user_id, "scenes")
    if await invalidate_scene_presentation(db, user_id, material_id, row.media_path):
        emit_ws_event(db, user_id=user_id, event_type="companion.life.presentation_requested", payload={"force": True})
    await db.delete(row)
    await db.flush()
    emit_ws_event(db, user_id=user_id, event_type="companion.scene.updated", payload={"scene_id": material_id})
