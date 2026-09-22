"""出镜媒体共用的固定身份、本次生成造型及视频参考准备。

造型来源统一命名：衣柜已启用外观、当前场景可见穿着、本次生成造型；
`outfit_override` 只作用于本次产物，不修改衣柜或当前场景。
"""

import asyncio
from dataclasses import dataclass

from components import SESSION_LOCAL, safe_json_loads
from modules.companion import AvatarAsset, CharacterCardSnapshot, CompanionOutfit
from prompts.generation import CHARACTER_REFERENCE_ALIGN, CHARACTER_VISUAL_STYLE
from sqlalchemy import select

from services.domains.companion import render_character_identity, require_character_snapshot
from services.infrastructure.assets import unlink_companion_asset

from .avatar_service import AvatarSourceUnreadableError, load_avatar_bytes_as_data_uri
from .image_generation import ImageGenerationError, generate_images


@dataclass(frozen=True)
class SelfVisualContext:
    identity: CharacterCardSnapshot
    reference_image: str
    outfit_description: str
    outfit_reference: str | None
    outfit_revision: int | None


@dataclass(frozen=True)
class SelfVisualPlan:
    """同一生成任务冻结的最终造型，重试和恢复沿用。"""

    context: SelfVisualContext
    override_outfit_description: str = ""


def _normalize_override(outfit_override: str | None) -> str:
    return (outfit_override or "").strip()


async def load_self_visual_context(user_id: int) -> SelfVisualContext:
    async with SESSION_LOCAL() as db:
        identity = await require_character_snapshot(db, user_id)
        avatar = await db.get(AvatarAsset, identity.avatar_id)
        if avatar is None:
            raise AvatarSourceUnreadableError("角色形象缺失")
        seed_path = avatar.seed_fullbody_url
        outfit = await db.scalar(
            select(CompanionOutfit).where(
                CompanionOutfit.user_id == user_id,
                CompanionOutfit.active.is_(True),
                CompanionOutfit.status == "ready",
            ),
        )
        outfit_path = outfit.fullbody_url if outfit is not None else ""
        outfit_description = (outfit.description or "") if outfit is not None else ""
        source = safe_json_loads(outfit.source_json, default={}) if outfit is not None else {}
        revision = source.get("character_card_revision") if isinstance(source, dict) else None
    reference = await asyncio.to_thread(load_avatar_bytes_as_data_uri, seed_path)
    if not reference:
        raise AvatarSourceUnreadableError("全身形象缺失或无法读取，请重新生成")
    outfit_reference = await asyncio.to_thread(load_avatar_bytes_as_data_uri, outfit_path) if outfit_path else None
    return SelfVisualContext(
        identity=identity,
        reference_image=reference,
        outfit_description=outfit_description,
        outfit_reference=outfit_reference,
        outfit_revision=revision if isinstance(revision, int) else None,
    )


def apply_outfit_override(context: SelfVisualContext, outfit_override: str | None) -> SelfVisualPlan:
    override = _normalize_override(outfit_override)
    return SelfVisualPlan(context=context, override_outfit_description=override)


def plan_outfit_description(plan: SelfVisualPlan) -> str:
    return plan.override_outfit_description or plan.context.outfit_description


async def align_character_reference(
    user_id: int,
    reference_image: str,
    identity: CharacterCardSnapshot,
    outfit_description: str,
) -> str:
    """返回本次生成独有的持久参考；调用者承担保存或回收，不改变原图。"""
    paths = await generate_images(
        CHARACTER_REFERENCE_ALIGN.format(identity=render_character_identity(identity), outfit=outfit_description)
        + "\n"
        + CHARACTER_VISUAL_STYLE,
        user_id=user_id,
        reference_image=reference_image,
        size="1024x1792",
        image_edit=True,
        persist_user_assets=True,
    )
    return paths[0]


def needs_identity_alignment(identity: CharacterCardSnapshot, applied_revision: int | None) -> bool:
    # 修订后恢复自动值仍须校准旧参考；无手改标记不代表图片已应用当前资料。
    return applied_revision != identity.revision and (
        identity.revision > 1 or bool(identity.overrides.model_dump(exclude_none=True))
    )


async def prepare_self_video_reference(plan: SelfVisualPlan, user_id: int, frame: str | None = None) -> str:
    """视频首帧参考；覆盖生效时不复用不相符的衣柜图，改按角色卡派生参考。"""
    context = plan.context
    override = plan.override_outfit_description
    reference = frame or context.reference_image
    outfit_reference_usable = not override
    if frame is None and outfit_reference_usable:
        reference = context.outfit_reference or context.reference_image
    applied_revision = (
        context.outfit_revision if frame is None and outfit_reference_usable and context.outfit_reference else None
    )
    if (
        frame is None
        and outfit_reference_usable
        and not needs_identity_alignment(context.identity, applied_revision)
        and (context.outfit_reference is not None or not context.outfit_description)
    ):
        return reference
    path = await align_character_reference(user_id, reference, context.identity, plan_outfit_description(plan))
    try:
        result = await asyncio.to_thread(load_avatar_bytes_as_data_uri, path)
        if not result:
            raise ImageGenerationError("无法读取应用角色资料后的参考图")
        return result
    finally:
        unlink_companion_asset(path)
