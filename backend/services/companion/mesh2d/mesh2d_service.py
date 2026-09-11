"""2D service — 业务逻辑层：在 finalize 后触发切分、查询活跃模型、切换渲染模式。"""

import json

from components import get_logger
from modules.companion import AvatarAsset, Companion2DModel, Companion2DModelResponse, Persona
from modules.ws import emit_ws_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import asset_store
from ..persona_service import get_or_create_persona
from .pipeline import active_model_ids, run_mesh2d_pipeline

logger = get_logger(__name__)


class Mesh2DNotReadyError(RuntimeError):
    """avatar 尚未就绪（无激活行或缺少正面种子图），无法启动 2D 切分。"""


async def _resolve_active_avatar(db: AsyncSession, user_id: int) -> AvatarAsset | None:
    return (
        await db.execute(
            select(AvatarAsset).where(
                AvatarAsset.user_id == user_id,
                AvatarAsset.active.is_(True),
            ),
        )
    ).scalar_one_or_none()


async def generate_mesh2d_model(
    db: AsyncSession,
    *,
    user_id: int,
    priority: str = "high",
    force: bool = False,
) -> Companion2DModel:
    """从已激活 avatar 启动 2d 切分；avatar 不可用时抛错。"""
    avatar = await _resolve_active_avatar(db, user_id)

    if avatar is None or not avatar.seed_front_2d_url:
        raise Mesh2DNotReadyError("请先完成形象生成后再启动 2D 切分")

    fullbody_url = avatar.seed_front_2d_url

    if not force:
        existing = (
            await db.execute(
                select(Companion2DModel).where(
                    Companion2DModel.user_id == user_id,
                    Companion2DModel.status == "generating",
                ),
            )
        ).scalar_one_or_none()

        if existing is not None:
            if existing.id in active_model_ids():
                logger.info(
                    "2d generation already in flight",
                    extra={"user_id": user_id, "model_id": existing.id},
                )
                return existing
            # 进程重启遗留的僵尸 generating 行（无在飞任务认领）：置失败后走新切分，
            # 否则用户会被一行死状态永久卡在 409-less 的"生成中"。
            existing.status = "failed"
            existing.error = "interrupted by restart"
            await db.commit()

        active = (
            await db.execute(
                select(Companion2DModel).where(
                    Companion2DModel.user_id == user_id,
                    Companion2DModel.active.is_(True),
                    Companion2DModel.status == "succeeded",
                ),
            )
        ).scalar_one_or_none()

        if active is not None and active.avatar_id == avatar.id:
            logger.info(
                "active 2d already exists",
                extra={"user_id": user_id, "model_id": active.id},
            )
            return active

    model = Companion2DModel(
        user_id=user_id,
        avatar_id=avatar.id,
        status="generating",
    )
    db.add(model)
    await db.commit()
    await db.refresh(model)

    # 后台执行不阻塞请求；就绪经 companion.2d.ready 事件下发（DESIGN §5.5 异步启动）
    run_mesh2d_pipeline(
        user_id=user_id,
        model_id=model.id,
        fullbody_url=fullbody_url,
        priority=priority,
    )
    logger.info(
        "2d pipeline kicked off",
        extra={"user_id": user_id, "model_id": model.id, "priority": priority},
    )
    return model


async def get_active_mesh2d_response(
    db: AsyncSession,
    user_id: int,
) -> Companion2DModelResponse | None:
    """把活跃 2d 模型转换为 API 响应；客户端拿到 manifest_url 后交给 puppet 渲染层装配 PSD。"""
    model = (
        await db.execute(
            select(Companion2DModel).where(
                Companion2DModel.user_id == user_id,
                Companion2DModel.active.is_(True),
                Companion2DModel.status == "succeeded",
            ),
        )
    ).scalar_one_or_none()

    if model is None:
        return None

    return mesh2d_response(model)


def mesh2d_response(model: Companion2DModel) -> Companion2DModelResponse:
    manifest_url = asset_store.signed_companion_asset_url(model.manifest_path) if model.manifest_path else None

    layer_urls: dict[str, str] = {}

    try:
        raw_layers = json.loads(model.layers_json or "[]")

        if isinstance(raw_layers, list):
            for entry in raw_layers:
                if isinstance(entry, dict) and entry.get("name") and entry.get("url"):
                    signed = asset_store.signed_companion_asset_url(entry["url"])
                    if signed:
                        layer_urls[entry["name"]] = signed
    except Exception as exc:
        logger.warning("failed to parse 2d layers_json", extra={"error": str(exc)})

    return Companion2DModelResponse(
        id=model.id,
        status=model.status,
        style=model.style or "cel_shading",
        manifest_url=manifest_url,
        layer_urls=layer_urls,
        content_hash=model.content_hash,
        error=model.error,
    )


async def set_render_mode(
    db: AsyncSession,
    *,
    user_id: int,
    render_mode: str,
) -> Persona:
    """写入 render_mode；切到 3D 时由调用方触发 3D 流水线（model_service）。"""
    persona = await get_or_create_persona(db, user_id)
    changed = persona.render_mode != render_mode
    persona.render_mode = render_mode
    if changed:
        # 多端同步：向所有在线端广播。仅在值实际变化时发——客户端收到后会回 POST 同步，
        # 无条件广播会形成自激回环。
        emit_ws_event(
            db,
            user_id=user_id,
            event_type="companion.render_mode.changed",
            payload={"new_mode": render_mode},
        )
    await db.commit()
    await db.refresh(persona)
    logger.info(
        "persona render_mode updated",
        extra={"user_id": user_id, "render_mode": render_mode},
    )

    return persona
