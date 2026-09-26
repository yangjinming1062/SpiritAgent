"""出镜媒体共用的固定身份、本次生成造型及视频参考准备。

造型来源统一命名：衣柜已启用外观、当前场景可见穿着、本次生成造型；
`outfit_override` 只作用于本次产物，不修改衣柜或当前场景。
"""

import asyncio
import io
from dataclasses import dataclass

from components import SESSION_LOCAL
from modules.companion import AvatarAsset, CharacterCardSnapshot, CompanionOutfit
from PIL import Image
from prompts.generation import (
    CHARACTER_ALIGNMENT_REFERENCES,
    CHARACTER_FRAME_ALIGN,
    CHARACTER_REFERENCE_ALIGN,
    CHARACTER_VISUAL_STYLE,
    SELF_IMAGE_CURRENT_OUTFIT,
    SELF_IMAGE_OUTFIT_DESCRIPTION,
    SELF_IMAGE_OUTFIT_REFERENCE,
    SELF_IMAGE_REFERENCE_TEMPLATE,
    SELF_VIDEO_FIRST_FRAME,
)
from sqlalchemy import select

from services.domains.companion import render_character_identity, require_character_snapshot
from services.infrastructure.assets import unlink_companion_asset
from services.infrastructure.llm import ASPECT_RATIOS, resolve_reference_bytes

from .avatar_service import AvatarSourceUnreadableError, load_avatar_bytes_as_data_uri
from .character_images import ImageChainState, ImageProgressWriter, generate_character_images
from .image_generation import ImageGenerationError, resolve_image_gen_chain


@dataclass(frozen=True)
class SelfVisualContext:
    identity: CharacterCardSnapshot
    reference_image: str
    reference_path: str
    outfit_description: str
    outfit_reference: str | None


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
    reference = await asyncio.to_thread(load_avatar_bytes_as_data_uri, seed_path)
    if not reference:
        raise AvatarSourceUnreadableError("全身形象缺失或无法读取，请重新生成")
    outfit_reference = await asyncio.to_thread(load_avatar_bytes_as_data_uri, outfit_path) if outfit_path else None
    return SelfVisualContext(
        identity=identity,
        reference_image=reference,
        reference_path=seed_path,
        outfit_description=outfit_description,
        outfit_reference=outfit_reference,
    )


def apply_outfit_override(context: SelfVisualContext, outfit_override: str | None) -> SelfVisualPlan:
    override = _normalize_override(outfit_override)
    return SelfVisualPlan(context=context, override_outfit_description=override)


def plan_outfit_description(plan: SelfVisualPlan) -> str:
    return plan.override_outfit_description or plan.context.outfit_description


def build_self_image_prompt(plan: SelfVisualPlan, prompt: str, *, has_outfit_reference: bool) -> str:
    outfit = plan_outfit_description(plan)
    parts = [
        SELF_IMAGE_REFERENCE_TEMPLATE.format(
            reference="图 1" if has_outfit_reference else "参考图",
            outfit=SELF_IMAGE_OUTFIT_DESCRIPTION.format(outfit=outfit)
            if outfit and not has_outfit_reference
            else ("" if has_outfit_reference else SELF_IMAGE_CURRENT_OUTFIT),
            prompt=prompt,
        ),
    ]
    if has_outfit_reference:
        parts.append(SELF_IMAGE_OUTFIT_REFERENCE.format(outfit=outfit or "未提供"))
    parts.append(render_character_identity(plan.context.identity))
    return "\n".join(parts)


async def optional_outfit_image_reference(plan: SelfVisualPlan, user_id: int) -> str | None:
    """双图供应商可用时才附衣柜图；固定身份始终由全身种子提供。"""
    if plan.override_outfit_description or not plan.context.outfit_reference:
        return None
    async with SESSION_LOCAL() as db:
        chain, _ = await resolve_image_gen_chain(db, user_id, plan.context.reference_image, multiple_references=True)
    return plan.context.outfit_reference if chain else None


async def align_character_reference(
    user_id: int,
    reference_image: str,
    identity: CharacterCardSnapshot,
    outfit_description: str,
    *,
    preserve_frame: bool = False,
    identity_reference: str,
    state: ImageChainState | None = None,
    save_progress: ImageProgressWriter | None = None,
) -> str:
    """返回本次生成独有的持久参考；调用者承担保存或回收，不改变原图。"""
    size = "1024x1792"
    aspect = "9:16"
    if preserve_frame:
        raw, _ = await resolve_reference_bytes(reference_image)

        def frame_aspect() -> str:
            with Image.open(io.BytesIO(raw)) as image:
                ratio = image.width / image.height
            return min(ASPECT_RATIOS, key=lambda key: abs(ASPECT_RATIOS[key] - ratio))

        aspect = size = await asyncio.to_thread(frame_aspect)
    separate_reference = reference_image != identity_reference
    template = CHARACTER_FRAME_ALIGN if preserve_frame else CHARACTER_REFERENCE_ALIGN
    paths = await generate_character_images(
        (CHARACTER_ALIGNMENT_REFERENCES + "\n" if separate_reference else "")
        + template.format(
            target_reference="图 2" if separate_reference else "输入图",
            aspect=aspect,
            identity=render_character_identity(identity),
            outfit=outfit_description or "沿用原图可见造型",
        )
        + "\n"
        + CHARACTER_VISUAL_STYLE,
        user_id=user_id,
        reference_image=identity_reference,
        secondary_reference_image=reference_image if separate_reference else None,
        size=size,
        image_edit=not separate_reference,
        identity_reference=identity_reference,
        identity_text=render_character_identity(identity),
        state=state,
        save_progress=save_progress,
    )
    return paths[0]


def needs_identity_alignment(
    identity: CharacterCardSnapshot,
    applied_revision: int | None,
    *,
    identity_reference_path: str,
    applied_identity_reference_path: str | None,
) -> bool:
    if applied_identity_reference_path:
        return applied_identity_reference_path != identity_reference_path
    # 旧外观缺少身份图来源，沿用修订校验；文字资料不能作为校准目标。
    return applied_revision != identity.revision and (
        identity.revision > 1 or bool(identity.overrides.model_dump(exclude_none=True))
    )


async def prepare_self_video_reference(
    plan: SelfVisualPlan,
    user_id: int,
    frame: str | None = None,
    *,
    prompt: str,
    aspect_ratio: str | None = None,
) -> str:
    """新视频先生成符合要求的起始画面；显式首帧仅校准身份与明确的造型覆盖。"""
    context = plan.context
    if frame is None:
        outfit_reference = await optional_outfit_image_reference(plan, user_id)
        paths = await generate_character_images(
            build_self_image_prompt(
                plan,
                SELF_VIDEO_FIRST_FRAME.format(prompt=prompt),
                has_outfit_reference=bool(outfit_reference),
            ),
            user_id=user_id,
            reference_image=context.reference_image,
            secondary_reference_image=outfit_reference,
            identity_reference=context.reference_image,
            identity_text=render_character_identity(context.identity),
            size=aspect_ratio or "9:16",
        )
        path = paths[0]
    else:
        path = await align_character_reference(
            user_id,
            frame,
            context.identity,
            plan.override_outfit_description,
            preserve_frame=True,
            identity_reference=context.reference_image,
        )
    try:
        result = await asyncio.to_thread(load_avatar_bytes_as_data_uri, path)
        if not result:
            raise ImageGenerationError("无法读取视频起始画面")
        return result
    finally:
        unlink_companion_asset(path)
