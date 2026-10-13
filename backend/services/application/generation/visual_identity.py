"""出镜媒体共用的固定身份、本次生成造型及视频参考准备。衣柜已启用外观提供默认造型，本次生成造型可单独覆盖；`outfit_override` 只作用于本次产物，不改衣柜或场景。无角色出镜的图片与视频在此补上统一的真实拍摄要求。"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from components import SESSION_LOCAL, AiTaskKey
from modules.companion import AvatarAsset, BodyFeatures, CharacterCardSnapshot, CompanionOutfit
from prompts.generation import (
    CHARACTER_APPEARANCE_ALIGNMENT_REFERENCES,
    CHARACTER_APPEARANCE_REFERENCE_ALIGN,
    CHARACTER_APPEARANCE_VISUAL_STYLE,
    PLAIN_IMAGE_TEMPLATE,
    PLAIN_VIDEO_TEMPLATE,
    SELF_IMAGE_CURRENT_OUTFIT,
    SELF_IMAGE_OUTFIT_DESCRIPTION,
    SELF_IMAGE_OUTFIT_REFERENCE,
    SELF_IMAGE_REFERENCE_TEMPLATE,
    SELF_VIDEO_OUTFIT_REFERENCE,
    SELF_VIDEO_PORTRAIT_REFERENCE,
    SELF_VIDEO_REFERENCE_TEMPLATE,
)
from sqlalchemy import select

from services.domains.companion import render_character_identity, require_character_snapshot

from .avatar_service import FULLBODY_SIZE, AvatarSourceUnreadableError, load_avatar_bytes_as_data_uri
from .character_images import ImageChainState, ImageProgressWriter, generate_character_images


@dataclass(frozen=True)
class SelfVisualContext:
    # 全身种子路径只供任务身份代次守卫使用；媒体参考使用头像和当前着装图。
    identity: CharacterCardSnapshot
    avatar_image: str
    fullbody_path: str
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
        avatar_path = avatar.asset_url
        seed_path = avatar.seed_fullbody_url
        outfit = await db.scalar(
            select(CompanionOutfit).where(
                CompanionOutfit.user_id == user_id,
                CompanionOutfit.active.is_(True),
                CompanionOutfit.status == "ready",
            ),
        )
        if outfit is None or not outfit.fullbody_url:
            raise AvatarSourceUnreadableError("当前着装形象缺失，请先完成衣柜准备")
        outfit_path = outfit.fullbody_url
        outfit_description = outfit.description or ""
    reference = await asyncio.to_thread(load_avatar_bytes_as_data_uri, avatar_path)
    if not reference:
        raise AvatarSourceUnreadableError("头像缺失或无法读取，请重新生成")
    outfit_reference = await asyncio.to_thread(load_avatar_bytes_as_data_uri, outfit_path)
    if not outfit_reference:
        raise AvatarSourceUnreadableError("当前着装形象缺失或无法读取，请重新准备着装")
    return SelfVisualContext(
        identity=identity,
        avatar_image=reference,
        fullbody_path=seed_path,
        outfit_description=outfit_description,
        outfit_reference=outfit_reference,
    )


def apply_outfit_override(context: SelfVisualContext, outfit_override: str | None) -> SelfVisualPlan:
    return SelfVisualPlan(context=context, override_outfit_description=(outfit_override or "").strip())


def build_self_image_prompt(plan: SelfVisualPlan, prompt: str, *, has_outfit_reference: bool) -> str:
    outfit = plan.override_outfit_description or plan.context.outfit_description
    parts = [
        SELF_IMAGE_REFERENCE_TEMPLATE.format(
            reference="图 1 头像" if has_outfit_reference else "头像参考图",
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


def build_plain_image_prompt(prompt: str) -> str:
    """没有角色参考的图片：统一按真实拍摄呈现。"""
    return PLAIN_IMAGE_TEMPLATE.format(prompt=prompt)


def build_plain_video_prompt(prompt: str) -> str:
    return PLAIN_VIDEO_TEMPLATE.format(prompt=prompt)


def optional_outfit_image_reference(plan: SelfVisualPlan) -> str | None:
    """返回当前完整着装参考；不在这里静默丢弃必需的第二张参考图。"""
    if plan.override_outfit_description or not plan.context.outfit_reference:
        return None
    return plan.context.outfit_reference


async def align_character_reference(
    user_id: int,
    outfit_image: str,
    identity: CharacterCardSnapshot,
    outfit_description: str,
    *,
    task: AiTaskKey,
    identity_reference: str,
    storage_directory: str,
    state: ImageChainState | None = None,
    save_progress: ImageProgressWriter | None = None,
    before_submit: Callable[[], Awaitable[None]] | None = None,
) -> str:
    """返回本次生成独有的持久参考；调用者承担保存或回收，不改变原图。"""
    paths = await generate_character_images(
        CHARACTER_APPEARANCE_ALIGNMENT_REFERENCES
        + "\n"
        + CHARACTER_APPEARANCE_REFERENCE_ALIGN.format(
            identity=render_character_identity(identity),
            outfit=outfit_description or "沿用图 2 可见造型",
        )
        + "\n"
        + CHARACTER_APPEARANCE_VISUAL_STYLE,
        user_id=user_id,
        task=task,
        storage_directory=storage_directory,
        reference_image=identity_reference,
        secondary_reference_image=outfit_image,
        size=FULLBODY_SIZE,
        image_edit=False,
        identity_reference=identity_reference,
        identity_text=render_character_identity(identity),
        state=state,
        save_progress=save_progress,
        before_submit=before_submit,
    )
    return paths[0]


def self_video_references(plan: SelfVisualPlan, reference_image: str | None = None) -> tuple[str, ...]:
    """头像提供面容参考，当前着装全身图提供完整外观；造型覆盖只消费文字。"""
    outfit = None if plan.override_outfit_description else reference_image or plan.context.outfit_reference
    identity = plan.context.avatar_image
    return (identity, outfit) if outfit else (identity,)


def build_self_video_prompt(plan: SelfVisualPlan, prompt: str, *, has_outfit_reference: bool) -> str:
    outfit = plan.override_outfit_description or plan.context.outfit_description
    if has_outfit_reference:
        outfit_instructions = SELF_VIDEO_OUTFIT_REFERENCE
    elif outfit:
        outfit_instructions = SELF_IMAGE_OUTFIT_DESCRIPTION.format(outfit=outfit)
    else:
        outfit_instructions = SELF_IMAGE_CURRENT_OUTFIT
    reference = SELF_VIDEO_PORTRAIT_REFERENCE
    if not has_outfit_reference:
        body = plan.context.identity.features.model_dump(include=set(BodyFeatures.model_fields), exclude_defaults=True)
        if body:
            reference += "身体特征补充资料（JSON）：" + json.dumps(body, ensure_ascii=False) + "。"
    return SELF_VIDEO_REFERENCE_TEMPLATE.format(
        reference=reference,
        outfit=outfit_instructions,
        prompt=prompt,
    )
