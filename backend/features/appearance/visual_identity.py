"""出镜媒体共用的固定身份、本次生成造型及视频参考准备。衣柜已启用外观提供默认造型，本次生成造型可单独覆盖；`outfit_override` 只作用于本次产物，不改衣柜或场景。无角色出镜的图片与视频在此补上统一的真实拍摄要求。"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from infrastructure.ai.selection import ProviderSelection
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.assets.asset_store import read_asset_data_uri
from infrastructure.database import SESSION_LOCAL
from sqlalchemy import select

from features.appearance.avatar_service import FULLBODY_SIZE, AvatarSourceUnreadableError
from features.appearance.character_card import render_character_identity, require_character_snapshot
from features.appearance.models import AvatarAsset, CompanionOutfit
from features.appearance.queries import outfit_identity_matches
from features.media.character_images import ImageChainState, ImageProgressWriter, generate_character_images
from features.media.identity_contracts import BodyFeatures, CharacterCardSnapshot, CharacterReferenceRole
from features.media.prompts import (
    CHARACTER_APPEARANCE_ALIGNMENT_REFERENCES,
    CHARACTER_APPEARANCE_FULLBODY_REFERENCE_ALIGN,
    CHARACTER_APPEARANCE_FULLBODY_REFERENCES,
    CHARACTER_APPEARANCE_REFERENCE_ALIGN,
    CHARACTER_APPEARANCE_VISUAL_STYLE,
    PLAIN_IMAGE_TEMPLATE,
    PLAIN_VIDEO_TEMPLATE,
    SELF_IMAGE_CURRENT_OUTFIT,
    SELF_IMAGE_OUTFIT_DESCRIPTION,
    SELF_IMAGE_OUTFIT_REFERENCE,
    SELF_IMAGE_PORTRAIT_REFERENCES,
    SELF_IMAGE_REFERENCE_TEMPLATE,
    SELF_IMAGE_STYLING_REFERENCE,
    SELF_VIDEO_FULLBODY_REFERENCE,
    SELF_VIDEO_OUTFIT_REFERENCE,
    SELF_VIDEO_PORTRAIT_REFERENCE,
    SELF_VIDEO_REFERENCE_TEMPLATE,
    SELF_VIDEO_STYLING_REFERENCE,
)


@dataclass(frozen=True)
class SelfVisualContext:
    identity: CharacterCardSnapshot
    identity_image: str
    fullbody_path: str
    outfit_description: str
    outfit_reference: str | None
    identity_reference_role: CharacterReferenceRole = "portrait"


@dataclass(frozen=True)
class SelfVisualPlan:
    """同一生成任务冻结的最终造型，重试和恢复沿用。"""

    context: SelfVisualContext
    override_outfit_description: str = ""


async def load_self_visual_context(user_id: int, *, use_outfit_reference: bool = True) -> SelfVisualContext:
    async with SESSION_LOCAL() as db:
        identity = await require_character_snapshot(db, user_id)
        avatar = await db.get(AvatarAsset, identity.avatar_id)
        if avatar is None:
            raise AvatarSourceUnreadableError("角色形象缺失")
        identity_path = avatar.asset_url
        seed_path = avatar.seed_fullbody_url
        role: CharacterReferenceRole = "portrait"
        outfit_path, outfit_description = "", ""
        if use_outfit_reference:
            outfit = await db.scalar(
                select(CompanionOutfit).where(
                    CompanionOutfit.user_id == user_id,
                    CompanionOutfit.active.is_(True),
                    CompanionOutfit.status == "ready",
                ),
            )
            if outfit is None or not outfit.fullbody_url:
                raise AvatarSourceUnreadableError("当前着装形象缺失，请先完成衣柜准备")
            if not outfit_identity_matches(outfit.source_json, seed_path):
                identity_path, role = seed_path, "fullbody"
            outfit_path = outfit.fullbody_url
            outfit_description = outfit.description or ""
    reference = await asyncio.to_thread(read_asset_data_uri, identity_path)
    if not reference:
        raise AvatarSourceUnreadableError("身份参考缺失或无法读取，请重新生成")
    outfit_reference = await asyncio.to_thread(read_asset_data_uri, outfit_path) if outfit_path else None
    if use_outfit_reference and not outfit_reference:
        raise AvatarSourceUnreadableError("当前着装形象缺失或无法读取，请重新准备着装")
    return SelfVisualContext(
        identity=identity,
        identity_image=reference,
        fullbody_path=seed_path,
        outfit_description=outfit_description,
        outfit_reference=outfit_reference,
        identity_reference_role=role,
    )


def apply_outfit_override(context: SelfVisualContext, outfit_override: str | None) -> SelfVisualPlan:
    return SelfVisualPlan(context=context, override_outfit_description=(outfit_override or "").strip())


def build_self_image_prompt(plan: SelfVisualPlan, prompt: str, *, has_outfit_reference: bool) -> str:
    outfit = plan.override_outfit_description or plan.context.outfit_description
    fullbody = plan.context.identity_reference_role == "fullbody"
    parts = [
        SELF_IMAGE_REFERENCE_TEMPLATE.format(
            reference="图 1 全身身份图" if fullbody else "图 1 头像" if has_outfit_reference else "头像参考图",
            reference_roles=CHARACTER_APPEARANCE_FULLBODY_REFERENCES if fullbody else SELF_IMAGE_PORTRAIT_REFERENCES,
            outfit=SELF_IMAGE_OUTFIT_DESCRIPTION.format(outfit=outfit)
            if outfit and not has_outfit_reference
            else ("" if has_outfit_reference else SELF_IMAGE_CURRENT_OUTFIT),
            prompt=prompt,
        ),
    ]
    if has_outfit_reference:
        template = SELF_IMAGE_STYLING_REFERENCE if fullbody else SELF_IMAGE_OUTFIT_REFERENCE
        parts.append(template.format(outfit=outfit or "未提供"))
    parts.append(render_character_identity(plan.context.identity))
    return "\n".join(parts)


def build_plain_image_prompt(prompt: str) -> str:
    """没有角色参考的图片：统一按真实拍摄呈现。"""
    return PLAIN_IMAGE_TEMPLATE.format(prompt=prompt)


def build_plain_video_prompt(prompt: str) -> str:
    return PLAIN_VIDEO_TEMPLATE.format(prompt=prompt)


def optional_outfit_image_reference(plan: SelfVisualPlan) -> str | None:
    """返回当前完整着装参考；不在这里静默丢弃必需的第二张参考图。"""
    if plan.override_outfit_description:
        return None
    if not plan.context.outfit_reference:
        raise AvatarSourceUnreadableError("当前着装形象缺失或无法读取，请重新准备着装")
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
    identity_reference_role: CharacterReferenceRole = "portrait",
    state: ImageChainState | None = None,
    save_progress: ImageProgressWriter | None = None,
    before_submit: Callable[[], Awaitable[None]] | None = None,
    selection: ProviderSelection | None = None,
) -> str:
    """返回本次生成独有的持久参考；调用者承担保存或回收，不改变原图。"""
    references, alignment = (
        (CHARACTER_APPEARANCE_FULLBODY_REFERENCES, CHARACTER_APPEARANCE_FULLBODY_REFERENCE_ALIGN)
        if identity_reference_role == "fullbody"
        else (CHARACTER_APPEARANCE_ALIGNMENT_REFERENCES, CHARACTER_APPEARANCE_REFERENCE_ALIGN)
    )
    paths = await generate_character_images(
        references
        + "\n"
        + alignment.format(
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
        identity_reference_mode="primary" if identity_reference_role == "fullbody" else "portrait_and_outfit",
        goal_review="required",
        identity_text=render_character_identity(identity),
        state=state,
        save_progress=save_progress,
        before_submit=before_submit,
        selection=selection,
    )
    return paths[0]


def self_video_references(
    plan: SelfVisualPlan,
    reference_image: str | None = None,
    *,
    identity_reference: str | None = None,
) -> tuple[str, ...]:
    """沿冻结分工消费默认参考；显式造型图须搭配完整身份参考。"""
    if plan.override_outfit_description:
        return (plan.context.identity_image,)
    if reference_image:
        if not identity_reference:
            raise AvatarSourceUnreadableError("全身身份参考缺失或无法读取")
        return identity_reference, reference_image
    outfit = optional_outfit_image_reference(plan)
    return (plan.context.identity_image, outfit) if outfit else (plan.context.identity_image,)


def build_self_video_prompt(
    plan: SelfVisualPlan,
    prompt: str,
    *,
    has_outfit_reference: bool,
    identity_reference_role: CharacterReferenceRole | None = None,
) -> str:
    identity_reference_role = identity_reference_role or plan.context.identity_reference_role
    outfit = plan.override_outfit_description or plan.context.outfit_description
    if has_outfit_reference:
        outfit_instructions = (
            SELF_VIDEO_STYLING_REFERENCE if identity_reference_role == "fullbody" else SELF_VIDEO_OUTFIT_REFERENCE
        )
    elif outfit:
        outfit_instructions = SELF_IMAGE_OUTFIT_DESCRIPTION.format(outfit=outfit)
    else:
        outfit_instructions = SELF_IMAGE_CURRENT_OUTFIT
    reference = (
        SELF_VIDEO_FULLBODY_REFERENCE if identity_reference_role == "fullbody" else SELF_VIDEO_PORTRAIT_REFERENCE
    )
    if not has_outfit_reference:
        body = plan.context.identity.features.model_dump(include=set(BodyFeatures.model_fields), exclude_defaults=True)
        if body:
            reference += "身体特征补充资料（JSON）：" + json.dumps(body, ensure_ascii=False) + "。"
    return SELF_VIDEO_REFERENCE_TEMPLATE.format(
        reference=reference,
        outfit=outfit_instructions,
        prompt=prompt,
    )
