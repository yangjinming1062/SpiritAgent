"""出镜媒体共用的固定身份、本次生成造型及视频参考准备。造型来源统一命名（衣柜已启用外观 / 当前场景可见穿着 / 本次生成造型）；`outfit_override` 只作用于本次产物，不改衣柜或场景。"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from components import SESSION_LOCAL
from modules.companion import AvatarAsset, CharacterCardSnapshot, CompanionOutfit
from prompts.generation import (
    CHARACTER_ALIGNMENT_REFERENCES,
    CHARACTER_REFERENCE_ALIGN,
    CHARACTER_VISUAL_STYLE,
    SELF_IMAGE_CURRENT_OUTFIT,
    SELF_IMAGE_OUTFIT_DESCRIPTION,
    SELF_IMAGE_OUTFIT_REFERENCE,
    SELF_IMAGE_REFERENCE_TEMPLATE,
    SELF_VIDEO_REFERENCE_TEMPLATE,
)
from sqlalchemy import select

from services.domains.companion import render_character_identity, require_character_snapshot

from .avatar_service import FULLBODY_SIZE, AvatarSourceUnreadableError, load_avatar_bytes_as_data_uri
from .character_images import ImageChainState, ImageProgressWriter, generate_character_images
from .image_generation import resolve_image_gen_chain


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
    return SelfVisualPlan(context=context, override_outfit_description=(outfit_override or "").strip())


def build_self_image_prompt(plan: SelfVisualPlan, prompt: str, *, has_outfit_reference: bool) -> str:
    outfit = plan.override_outfit_description or plan.context.outfit_description
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
    if (
        plan.override_outfit_description
        or not plan.context.outfit_reference
        or plan.context.outfit_reference == plan.context.reference_image
    ):
        return None
    async with SESSION_LOCAL() as db:
        chain, _ = await resolve_image_gen_chain(db, user_id, has_reference=True, multiple_references=True)
    return plan.context.outfit_reference if chain else None


async def align_character_reference(
    user_id: int,
    reference_image: str,
    identity: CharacterCardSnapshot,
    outfit_description: str,
    *,
    identity_reference: str,
    state: ImageChainState | None = None,
    save_progress: ImageProgressWriter | None = None,
    before_submit: Callable[[], Awaitable[None]] | None = None,
) -> str:
    """返回本次生成独有的持久参考；调用者承担保存或回收，不改变原图。"""
    separate_reference = reference_image != identity_reference
    paths = await generate_character_images(
        (CHARACTER_ALIGNMENT_REFERENCES + "\n" if separate_reference else "")
        + CHARACTER_REFERENCE_ALIGN.format(
            target_reference="图 2" if separate_reference else "输入图",
            identity=render_character_identity(identity),
            outfit=outfit_description or "沿用原图可见造型",
        )
        + "\n"
        + CHARACTER_VISUAL_STYLE,
        user_id=user_id,
        reference_image=identity_reference,
        secondary_reference_image=reference_image if separate_reference else None,
        size=FULLBODY_SIZE,
        image_edit=not separate_reference,
        identity_reference=identity_reference,
        identity_text=render_character_identity(identity),
        state=state,
        save_progress=save_progress,
        before_submit=before_submit,
    )
    return paths[0]


def self_video_references(plan: SelfVisualPlan, reference_image: str | None = None) -> tuple[str, ...]:
    """身份与选定造型分别提供参考；相同图只发送一次，造型覆盖只消费文字。"""
    outfit = None if plan.override_outfit_description else reference_image or plan.context.outfit_reference
    identity = plan.context.reference_image
    return (identity, outfit) if outfit and outfit != identity else (identity,)


def build_self_video_prompt(plan: SelfVisualPlan, prompt: str, *, has_outfit_reference: bool) -> str:
    outfit = plan.override_outfit_description or plan.context.outfit_description
    if has_outfit_reference:
        outfit_instructions = SELF_IMAGE_OUTFIT_REFERENCE.format(outfit="未提供")
    elif outfit:
        outfit_instructions = SELF_IMAGE_OUTFIT_DESCRIPTION.format(outfit=outfit)
    else:
        outfit_instructions = SELF_IMAGE_CURRENT_OUTFIT
    return (
        SELF_VIDEO_REFERENCE_TEMPLATE.format(
            reference="图 1" if has_outfit_reference else "参考图",
            outfit=outfit_instructions,
            prompt=prompt,
        )
        + "\n"
        + render_character_identity(plan.context.identity)
    )
