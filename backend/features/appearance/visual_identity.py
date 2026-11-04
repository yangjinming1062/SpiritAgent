"""出镜媒体共用的固定身份、本次生成造型及视频参考准备。实际呈现快照提供默认造型，本次生成造型可单独覆盖；`outfit_override` 只作用于本次产物，不改素材或影像库。无角色出镜的图片与视频在此补上统一的真实拍摄要求。"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from infrastructure.ai.selection import ProviderSelection
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.assets.image_references import require_owned_image_path
from infrastructure.database import SESSION_LOCAL

from features.appearance.avatar_service import FULLBODY_SIZE, AvatarSourceUnreadableError
from features.appearance.identity import require_visual_identity
from features.appearance.models import AvatarAsset
from features.media.character_images import ImageChainState, ImageProgressWriter, generate_reviewed_images
from features.media.identity_contracts import CharacterReferenceRole, VisualIdentitySnapshot
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
from features.visuals.schemas import ActualPresentationSnapshot


@dataclass(frozen=True)
class SelfVisualContext:
    user_id: int
    identity: VisualIdentitySnapshot
    identity_path: str
    fullbody_path: str
    outfit_description: str
    outfit_path: str | None
    identity_reference_role: CharacterReferenceRole = "portrait"


@dataclass(frozen=True)
class SelfVisualPlan:
    """同一生成任务冻结的最终造型，重试和恢复沿用。"""

    context: SelfVisualContext
    override_outfit_description: str = ""


def _identity_reference_mode(role: CharacterReferenceRole) -> Literal["primary", "portrait_and_outfit"]:
    return "primary" if role == "fullbody" else "portrait_and_outfit"


async def load_self_visual_context(
    user_id: int,
    presentation: ActualPresentationSnapshot,
    *,
    outfit_override: str | None = None,
) -> SelfVisualPlan:
    """按调用方传入的实际呈现快照装配出镜造型：窗口造型取静态造型素材（outfit_path/outfit_description），实际呈现已有成品画面时以 visual_description 为准。override 非空即关造型参考，只作用于本次产物。素材缺失时明确失败。"""
    override = (outfit_override or "").strip()
    async with SESSION_LOCAL() as db:
        identity = await require_visual_identity(db, user_id)
        avatar = await db.get(AvatarAsset, identity.avatar_id)
        if avatar is None:
            raise AvatarSourceUnreadableError("角色形象缺失")
        seed_path = avatar.seed_fullbody_url
        role: CharacterReferenceRole = (
            "fullbody" if override or presentation.visual_path else presentation.identity_reference_role
        )
        identity_path = seed_path if role == "fullbody" else avatar.asset_url
        outfit_path, outfit_description = "", ""
        if not override:
            outfit_path = presentation.visual_path or presentation.outfit_path or ""
            if not outfit_path:
                raise AvatarSourceUnreadableError("当前呈现形象缺失，请先完成素材准备")
            outfit_description = presentation.outfit_description
    try:
        identity_path = await require_owned_image_path(identity_path, user_id=user_id)
    except (OSError, ValueError) as exc:
        raise AvatarSourceUnreadableError("身份参考缺失或无法读取，请重新生成") from exc
    try:
        outfit_path = await require_owned_image_path(outfit_path, user_id=user_id) if outfit_path else None
    except (OSError, ValueError) as exc:
        raise AvatarSourceUnreadableError("当前呈现形象缺失或无法读取，请重新准备呈现素材") from exc
    return SelfVisualPlan(
        context=SelfVisualContext(
            user_id=user_id,
            identity=identity,
            identity_path=identity_path,
            fullbody_path=seed_path,
            outfit_description=outfit_description,
            outfit_path=outfit_path,
            identity_reference_role=role,
        ),
        override_outfit_description=override,
    )


def build_self_image_prompt(plan: SelfVisualPlan, prompt: str, *, has_outfit_reference: bool) -> str:
    outfit = plan.override_outfit_description or plan.context.outfit_description
    fullbody = plan.context.identity_reference_role == "fullbody"
    parts = [
        SELF_IMAGE_REFERENCE_TEMPLATE.format(
            reference="图 1 全身身份图" if fullbody else "图 1 头像" if has_outfit_reference else "头像参考图",
            reference_roles=(
                CHARACTER_APPEARANCE_FULLBODY_REFERENCES if has_outfit_reference else SELF_VIDEO_FULLBODY_REFERENCE
            )
            if fullbody
            else SELF_IMAGE_PORTRAIT_REFERENCES,
            outfit=SELF_IMAGE_OUTFIT_DESCRIPTION.format(outfit=outfit)
            if outfit and not has_outfit_reference
            else ("" if has_outfit_reference else SELF_IMAGE_CURRENT_OUTFIT),
            prompt=prompt,
        ),
    ]
    if has_outfit_reference:
        template = SELF_IMAGE_STYLING_REFERENCE if fullbody else SELF_IMAGE_OUTFIT_REFERENCE
        parts.append(template.format(outfit=outfit or "未提供"))
    return "\n".join(parts)


def build_plain_image_prompt(prompt: str) -> str:
    """没有角色参考的图片：统一按真实拍摄呈现。"""
    return PLAIN_IMAGE_TEMPLATE.format(prompt=prompt)


def build_plain_video_prompt(prompt: str) -> str:
    return PLAIN_VIDEO_TEMPLATE.format(prompt=prompt)


def optional_outfit_image_reference(plan: SelfVisualPlan) -> str | None:
    """返回本次造型参考；不在这里静默丢弃必需的第二张参考图。"""
    if plan.override_outfit_description:
        return None
    if not plan.context.outfit_path:
        raise AvatarSourceUnreadableError("当前呈现形象缺失或无法读取，请重新准备呈现素材")
    return plan.context.outfit_path


@dataclass(frozen=True)
class SelfImageInputs:
    """一次出镜图片的冻结提示词与参考参数，调用方原样传给生成入口。"""

    prompt: str
    identity: VisualIdentitySnapshot
    reference_image: str
    secondary_reference_image: str | None
    identity_reference_mode: Literal["primary", "portrait_and_outfit"]


def build_self_image_inputs(plan: SelfVisualPlan, prompt: str) -> SelfImageInputs:
    outfit = optional_outfit_image_reference(plan)
    return SelfImageInputs(
        prompt=build_self_image_prompt(plan, prompt, has_outfit_reference=bool(outfit)),
        identity=plan.context.identity,
        reference_image=plan.context.identity_path,
        secondary_reference_image=outfit,
        identity_reference_mode=_identity_reference_mode(plan.context.identity_reference_role),
    )


async def align_character_reference(
    user_id: int,
    outfit_image: str,
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
    paths = await generate_reviewed_images(
        references
        + "\n"
        + alignment.format(
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
        identity_reference_mode=_identity_reference_mode(identity_reference_role),
        goal_review="required",
        state=state,
        save_progress=save_progress,
        before_submit=before_submit,
        selection=selection,
    )
    return paths[0]


@dataclass(frozen=True)
class SelfVideoInputs:
    """一次出镜视频的冻结提示词与参考参数，调用方原样传给生成入口。"""

    prompt: str
    references: tuple[str, ...]
    identity_reference_role: CharacterReferenceRole


async def build_self_video_inputs(
    plan: SelfVisualPlan,
    prompt: str,
    reference_image: str | None = None,
) -> SelfVideoInputs:
    """冻结视频提示词、参考图与身份职责；has_outfit_reference 与 role 随参考分支一次确定。"""
    identity_reference_role: CharacterReferenceRole = plan.context.identity_reference_role
    references: tuple[str, ...]
    if plan.override_outfit_description:
        references = (plan.context.identity_path,)
    elif reference_image:
        try:
            await require_owned_image_path(plan.context.fullbody_path, user_id=plan.context.user_id)
        except (OSError, ValueError) as exc:
            raise AvatarSourceUnreadableError("全身身份参考缺失或无法读取") from exc
        identity_reference_role = "fullbody"
        references = (plan.context.fullbody_path, reference_image)
    else:
        outfit_reference = optional_outfit_image_reference(plan)
        references = (
            (plan.context.identity_path, outfit_reference) if outfit_reference else (plan.context.identity_path,)
        )
    has_outfit_reference = len(references) > 1
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
    return SelfVideoInputs(
        prompt=SELF_VIDEO_REFERENCE_TEMPLATE.format(reference=reference, outfit=outfit_instructions, prompt=prompt),
        references=references,
        identity_reference_role=identity_reference_role,
    )
