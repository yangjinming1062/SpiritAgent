"""Outfit service —— 2D 换装外观生命周期：草稿生成 → 确认转正切分 → 穿着 / 删除。

服装 / 发型是可换元素而非身份变更（DESIGN §5.4 形象锁定的豁免，同背面种子先例）：
身份锚点恒为激活头像行的独立全身种子图（避免派生图迭代失真），本服务不检查 raise_if_image_sealed。
两段式激活不变量：切分完成前旧 2d 行保持激活，翻转只发生在 2d 管线的成功
接缝（2d pipeline.py）；提前翻转会令 get_active_mesh2d_response 落空、精灵掉蛋。
所有外观状态校验与翻转（含管线接缝）共用用户级锁——锁外校验会让并发双击确认
插出两行切分任务、或令在途切分覆盖用户手选。
"""

import asyncio
import base64
import contextlib
import json
from datetime import timedelta
from typing import Literal

from components import (
    DEFAULT_LANGUAGE,
    SESSION_LOCAL,
    get_logger,
    parse_llm_json,
    resolve_language,
    safe_json_loads,
    track_user_task,
    utc_now,
)
from modules.companion import (
    OUTFIT_POLICY_DEFAULT,
    AvatarAsset,
    Companion2DModel,
    CompanionOutfit,
    ImageReviseMode,
    OutfitResponse,
    Persona,
)
from modules.settings import UserSetting
from modules.ws import emit_ws_event
from prompts.generation import EDIT_PRESERVE_IDENTITY, OUTFIT_DESCRIBE_SYSTEM
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from services.domains.companion import get_or_create_persona, load_persona_definition
from services.infrastructure.assets import resolve_companion_asset_path
from services.infrastructure.llm import (
    build_image_edit_prompt,
    build_outfit_prompt,
    chat,
    describe_garment_image,
    resolve_fullbody_template,
)

from .avatar_service import (
    _fullbody_aspect_for,
    _fullbody_size_for,
    _generate_one_portrait_with_moderation_retry,
    _persist_portrait_bytes,
    _persist_portrait_or_draft,
    _read_temp_media_bytes,
    _resolve_fullbody_rig_type,
    delete_portrait_file,
    get_avatar_job_lock,
    load_avatar_bytes_as_data_uri,
    resolve_uploaded_avatar_path,
)
from .mesh2d import (
    active_model_ids,
    build_pose_side_prompt,
    mesh2d_response,
    pose_backdrop_for_artwork,
    pose_regeneration_in_progress,
    run_mesh2d_pipeline,
    run_pose_side_regeneration,
)
from .response_builders import outfit_response

logger = get_logger(__name__)

# temp-media 草稿 24h TTL，留 1h 余量在读取时清扫过期草稿
_DRAFT_TTL = timedelta(hours=23)
_SPLITTING_TIMEOUT = timedelta(minutes=30)
_DESCRIBE_TASKS: set[asyncio.Task[None]] = set()


class OutfitError(RuntimeError):
    """换装流程错误；str(exc) 恒为可展示的公开文案。"""


class OutfitNotFoundError(OutfitError):
    """目标外观行不存在或不属于调用者。"""


class OutfitStateError(OutfitError):
    """状态守卫拒绝（无 2D 身体 / 切分进行中 / 非法状态转换 / 删除保护）。"""


class OutfitDraftExpiredError(OutfitError):
    """草稿立绘的 temp-media 文件已过期，需重新生成。"""


async def _require_fullbody_seed_readable(avatar: AvatarAsset) -> str:
    """换装/自备图身份锚点：全身种子路径存在且字节可读，返回 data URI。

    不可读是源资产状态冲突（与 avatar/room 自备图、AI 换装同语义），抛 OutfitStateError
    由 API 映射为 409，不能降级纯文字。"""
    if not avatar.seed_fullbody_url:
        raise OutfitStateError("全身种子图缺失或无法读取，请在设置的“角色与记忆”中重新生成")
    uri = await asyncio.to_thread(load_avatar_bytes_as_data_uri, avatar.seed_fullbody_url)
    if uri is None:
        raise OutfitStateError("全身种子图缺失或无法读取，请在设置的“角色与记忆”中重新生成")
    return uri


async def get_outfit_policy(db: AsyncSession, user_id: int) -> str:
    policy = await db.scalar(
        select(Persona.outfit_policy).where(Persona.user_id == user_id),
    )
    return policy or OUTFIT_POLICY_DEFAULT


async def set_outfit_policy(db: AsyncSession, user_id: int, policy: str) -> str:
    if policy not in ("locked", "llm_may_replace"):
        raise OutfitStateError(f"unknown outfit policy: {policy}")
    persona = await get_or_create_persona(db, user_id)
    persona.outfit_policy = policy
    await db.commit()
    return policy


async def _get_outfit(
    db: AsyncSession,
    user_id: int,
    outfit_id: int,
) -> CompanionOutfit | None:
    return (
        await db.execute(
            select(CompanionOutfit)
            .where(CompanionOutfit.id == outfit_id, CompanionOutfit.user_id == user_id)
            .execution_options(populate_existing=True),
        )
    ).scalar_one_or_none()


async def _active_avatar(db: AsyncSession, user_id: int) -> AvatarAsset | None:
    return (
        await db.execute(
            select(AvatarAsset).where(
                AvatarAsset.user_id == user_id,
                AvatarAsset.active.is_(True),
            ),
        )
    ).scalar_one_or_none()


async def _active_mesh2d(db: AsyncSession, user_id: int) -> Companion2DModel | None:
    return (
        await db.execute(
            select(Companion2DModel).where(
                Companion2DModel.user_id == user_id,
                Companion2DModel.active.is_(True),
                Companion2DModel.status == "succeeded",
            ),
        )
    ).scalar_one_or_none()


async def _has_splitting(db: AsyncSession, user_id: int) -> bool:
    return (
        await db.execute(
            select(CompanionOutfit.id)
            .where(
                CompanionOutfit.user_id == user_id,
                CompanionOutfit.status == "splitting",
            )
            .limit(1),
        )
    ).scalar_one_or_none() is not None


async def _sweep_stale(db: AsyncSession, user_id: int) -> None:
    """读取时顺带清扫：过期草稿置 expired（参考图上传文件一并清理）、卡死的 splitting 置 failed。"""
    now = utc_now()
    rows = (
        (
            await db.execute(
                select(CompanionOutfit).where(
                    CompanionOutfit.user_id == user_id,
                    CompanionOutfit.status.in_(("draft", "splitting")),
                ),
            )
        )
        .scalars()
        .all()
    )
    changed = False
    for outfit in rows:
        age = now - outfit.updated_at
        if outfit.status == "draft" and age > _DRAFT_TTL:
            outfit.status = "expired"
            _delete_reference_file(outfit)
            changed = True
        elif outfit.status == "splitting" and age > _SPLITTING_TIMEOUT:
            model_id = (
                await db.execute(
                    select(Companion2DModel.id)
                    .where(
                        Companion2DModel.outfit_id == outfit.id,
                        Companion2DModel.status == "generating",
                    )
                    .order_by(Companion2DModel.id.desc())
                    .limit(1),
                )
            ).scalar_one_or_none()
            if model_id in active_model_ids():
                continue
            outfit.status = "failed"
            outfit.pending_wear = False
            changed = True
    if changed:
        await db.commit()


def _delete_reference_file(outfit: CompanionOutfit) -> None:
    source = safe_json_loads(outfit.source_json or "{}", default={})
    ref_path = source.get("reference_image_path") if isinstance(source, dict) else None
    if isinstance(ref_path, str) and ref_path:
        delete_portrait_file(ref_path)


async def _ensure_initial_outfit(db: AsyncSession, user_id: int) -> None:
    """衣柜为空时把当前形象合成第一套外观（回填 2d.outfit_id，此后激活翻转路径统一）；
    无就绪 2D 身体时保持空衣柜，由 UI 引导先生成形象。"""
    existing = (
        await db.execute(
            select(CompanionOutfit.id).where(CompanionOutfit.user_id == user_id).limit(1),
        )
    ).scalar_one_or_none()
    if existing is not None:
        return
    avatar = await _active_avatar(db, user_id)
    mesh2d = await _active_mesh2d(db, user_id)
    if avatar is None or mesh2d is None:
        return
    outfit = CompanionOutfit(
        user_id=user_id,
        name="初始形象",
        fullbody_url=avatar.seed_front_2d_url or avatar.asset_url,
        style=mesh2d.style or "refined_anime_cg",
        status="ready",
        active=True,
    )
    db.add(outfit)
    await db.flush()
    mesh2d.outfit_id = outfit.id
    await db.commit()
    _kick_describe(user_id, outfit.id)


async def list_outfits(db: AsyncSession, user_id: int) -> list[OutfitResponse]:
    async with get_avatar_job_lock(user_id):
        await _sweep_stale(db, user_id)
        await _ensure_initial_outfit(db, user_id)
        outfits = (
            (
                await db.execute(
                    select(CompanionOutfit)
                    .where(CompanionOutfit.user_id == user_id)
                    .order_by(CompanionOutfit.created_at.asc()),
                )
            )
            .scalars()
            .all()
        )
        models = (
            (
                await db.execute(
                    select(Companion2DModel)
                    .where(
                        Companion2DModel.user_id == user_id,
                        Companion2DModel.status == "succeeded",
                        Companion2DModel.outfit_id.is_not(None),
                    )
                    .order_by(Companion2DModel.outfit_id, Companion2DModel.id.desc())
                    .distinct(Companion2DModel.outfit_id),
                )
            )
            .scalars()
            .all()
        )
        assets = {model.outfit_id: mesh2d_response(model) for model in models}
        return [
            outfit_response(outfit).model_copy(
                update={"asset": assets.get(outfit.id) if outfit.status == "ready" else None},
            )
            for outfit in outfits
        ]


async def _outfit_generation_context(
    db: AsyncSession,
    user_id: int,
) -> tuple[AvatarAsset, Companion2DModel, str, str, str, str, str]:
    """返回 (激活头像, 激活 2d, 物种, 外貌, 性格, 画风, 骨骼类型)；守卫失败抛 OutfitStateError。"""
    avatar = await _active_avatar(db, user_id)
    mesh2d = await _active_mesh2d(db, user_id)
    if avatar is None or mesh2d is None:
        raise OutfitStateError("还没有就绪的 2D 形象，请先生成 2D 动画资产")
    if await _has_splitting(db, user_id):
        raise OutfitStateError("有一套外观正在生成中，请稍候")
    persona = await get_or_create_persona(db, user_id)
    if not persona.is_complete:
        raise OutfitStateError("请先完成 onboarding 再设计外观")
    definition = load_persona_definition(persona)
    prompt_payload = safe_json_loads(avatar.prompt_json or "{}", default={})
    style = (
        (prompt_payload.get("fullbody_style") if isinstance(prompt_payload, dict) else None)
        or mesh2d.style
        or "refined_anime_cg"
    )
    species = str(definition.get("biological_type") or "").strip()
    # 与正面种子同桶取 rig（缓存命中则零 LLM 调用）——换装立绘画幅/姿态与确认形象一致，衣柜内不漂移
    rig_type = await _resolve_fullbody_rig_type(db, user_id, avatar, species)
    return (
        avatar,
        mesh2d,
        species,
        str(definition.get("appearance") or "").strip(),
        str(definition.get("personality") or "").strip(),
        style,
        rig_type,
    )


async def _describe_reference_garment(
    user_id: int,
    source: dict,
    image: bytes | None = None,
    content_type: str | None = None,
    requirement: str = "",
) -> str | None:
    """把服装参考图连同用户文字要求整合为一段着装设计稿；视觉链缺失或整合失败返回 None（降级为纯描述生成）。

    image 缺省时从 source 指向的已转存参考图读取；成功时把设计稿写入 source（调用方持久化），
    重新生成时不再重复整合。整合走独立短会话，不占请求连接。"""
    if image is None:
        ref_path = source.get("reference_image_path")
        if not isinstance(ref_path, str) or not ref_path:
            return None
        resolved = resolve_uploaded_avatar_path(ref_path.rsplit("/", 1)[-1])
        if resolved is None:
            return None
        path, content_type = resolved
        try:
            image = await asyncio.to_thread(path.read_bytes)
        except OSError:
            return None
    try:
        garment_uri = await asyncio.to_thread(base64.b64encode, image)
        garment_uri = f"data:{content_type or 'image/png'};base64,{garment_uri.decode('ascii')}"
        text = await describe_garment_image(user_id, garment_uri, requirement)
    except Exception:
        logger.warning(
            "garment reference describe failed; falling back to text-only generation",
            extra={"user_id": user_id},
            exc_info=True,
        )
        return None
    source["reference_description"] = text
    return text


async def _generate_outfit_fullbody(
    user_id: int,
    *,
    species: str,
    rig_type: str,
    style: str,
    appearance: str,
    personality: str,
    feedback: str,
    identity_uri: str | None,
    prompt_override: str | None = None,
    image_edit: bool = False,
    edit_base_uri: str | None = None,
) -> str:
    """生成换装全身立绘草稿（persist=False 落 temp-media）；返回裸路径。画幅与姿态模板随 rig_type 分桶。

    微调模式传 prompt_override + edit_base_uri（编辑底图替换种子参考，提示词只含增量，identity_uri 为 None）。"""
    prompt = prompt_override or build_outfit_prompt(
        template=resolve_fullbody_template(species, rig_type, style),
        style_id=style,
        feedback=feedback,
        appearance=appearance,
        personality=personality,
    )
    draft_url, _, _, _ = await _generate_one_portrait_with_moderation_retry(
        prompt,
        user_id,
        reference_image=edit_base_uri if image_edit else identity_uri,
        size=_fullbody_size_for(rig_type),
        persist=False,
        image_edit=image_edit,
    )
    return draft_url


async def create_outfit_draft(
    db: AsyncSession,
    user_id: int,
    *,
    description: str | None,
    image: bytes | None = None,
    content_type: str | None = None,
) -> CompanionOutfit:
    """文字描述 + 可选参考图创建外观草稿；身份与身材参考恒为激活头像的独立全身种子图（唯一生图参考），
    参考图与文字要求先整合为一段着装描述再进提示词，参考图不直传生图。"""
    effective_description = (description or "").strip()
    if not effective_description and image is None:
        raise OutfitError("请先描述想要的着装，或上传一张参考图")

    (
        avatar,
        _,
        species,
        appearance,
        personality,
        style,
        rig_type,
    ) = await _outfit_generation_context(db, user_id)
    identity_uri = await _require_fullbody_seed_readable(avatar)
    # 结束读事务：整合与生图往返期间不占连接（短会话纪律）
    await db.commit()

    source: dict = {"description": effective_description}
    garment_text: str | None = None
    if image is not None:
        # 参考图立即转存 companion-avatars（temp-media 会过期，重新生成还要复用）
        ref_path, _, _ = await _persist_portrait_bytes(
            image,
            content_type or "image/png",
        )
        source["reference_image_path"] = ref_path
        # 失败降级为纯描述生成，下次重新生成会重试整合（整合走独立短会话，不占请求连接）
        garment_text = await _describe_reference_garment(
            user_id,
            source,
            image,
            content_type,
            requirement=effective_description,
        )

    # 着装描述恒为一段完整文本：设计稿已整合用户文字要求，缺设计稿时退回用户原话
    feedback = garment_text or effective_description or "为角色设计一套新的着装"

    draft_url = await _generate_outfit_fullbody(
        user_id,
        species=species,
        rig_type=rig_type,
        style=style,
        appearance=appearance,
        personality=personality,
        feedback=feedback,
        identity_uri=identity_uri,
    )

    async with get_avatar_job_lock(user_id):
        outfit = CompanionOutfit(
            user_id=user_id,
            name="新外观",
            fullbody_url=draft_url,
            style=style,
            status="draft",
            source_json=json.dumps(source, ensure_ascii=False),
        )
        db.add(outfit)
        await db.commit()
        await db.refresh(outfit)
    return outfit


async def regenerate_outfit_draft(
    db: AsyncSession,
    user_id: int,
    outfit_id: int,
    *,
    feedback: str | None,
    mode: ImageReviseMode,
) -> CompanionOutfit:
    """草稿或失败外观修改：mode="edit" 微调（编辑上一版立绘，未提及区域逐像素保留），
    mode="regenerate" 全量重绘（种子锚定）。两者成功后都回到草稿，重新确认才生成动画资产。"""
    outfit = await _get_outfit(db, user_id, outfit_id)
    if outfit is None:
        raise OutfitNotFoundError(f"outfit {outfit_id} not found")
    if outfit.status not in ("draft", "failed"):
        raise OutfitStateError("仅草稿或失败状态可以微调重绘")
    original_url = outfit.fullbody_url
    original_status = outfit.status

    effective_feedback = (feedback or "").strip()
    if mode == "edit" and not effective_feedback:
        raise OutfitError("请先描述要微调的内容")

    (
        avatar,
        _,
        species,
        appearance,
        personality,
        style,
        rig_type,
    ) = await _outfit_generation_context(db, user_id)

    source = safe_json_loads(outfit.source_json or "{}", default={})
    if not isinstance(source, dict):
        source = {}
    # 读取已完成：整合与生图往返期间不占连接（短会话纪律）
    await db.commit()

    if mode == "edit":
        # 编辑底图即上一版草稿立绘；身份已在其中，不重锚种子图（preserve 条款约束五官/身材不变）。
        edit_uri = await asyncio.to_thread(load_avatar_bytes_as_data_uri, original_url)
        if not edit_uri:
            raise OutfitDraftExpiredError("上一版草稿已过期或无法读取，请改用重新生成")
        prompt = build_image_edit_prompt(effective_feedback, preserve=EDIT_PRESERVE_IDENTITY)
        identity_uri = None
    else:
        identity_uri = await _require_fullbody_seed_readable(avatar)
        description = str(source.get("description") or "").strip()
        garment_text = str(source.get("reference_description") or "").strip()
        if not garment_text and source.get("reference_image_path"):
            # 旧草稿或上次整合失败：重新生成时补一次整合，成功则随本次 source_json 持久化
            garment_text = await _describe_reference_garment(user_id, source, requirement=description)
        # 着装描述恒为一段完整文本（设计稿已整合原始文字要求），再叠加本次修改要求
        combined_feedback = "；".join(part for part in (garment_text or description, effective_feedback) if part)
        prompt = build_outfit_prompt(
            template=resolve_fullbody_template(species, rig_type, style),
            style_id=style,
            feedback=combined_feedback,
            appearance=appearance,
            personality=personality,
        )

    draft_url = await _generate_outfit_fullbody(
        user_id,
        species=species,
        rig_type=rig_type,
        style=style,
        appearance=appearance,
        personality=personality,
        feedback="",
        identity_uri=identity_uri,
        prompt_override=prompt,
        image_edit=mode == "edit",
        edit_base_uri=edit_uri if mode == "edit" else None,
    )

    async with get_avatar_job_lock(user_id):
        # 生图期间可能已确认重试或被另一轮重绘替换，锁内刷新持久状态后再核对原始版本。
        outfit = await _get_outfit(db, user_id, outfit_id)
        if outfit is None or outfit.status != original_status or outfit.fullbody_url != original_url:
            if draft_url != original_url:
                delete_portrait_file(draft_url)
            raise OutfitStateError("外观已发生变化，请刷新后重试")
        outfit.fullbody_url = draft_url
        outfit.status = "draft"
        outfit.pending_wear = False
        if effective_feedback:
            source["feedback"] = effective_feedback
        outfit.source_json = json.dumps(source, ensure_ascii=False)
        emit_ws_event(
            db,
            user_id=user_id,
            event_type="companion.outfit.updated",
            payload={"outfit_id": outfit.id, "worn": False},
        )
        await db.commit()
        if original_url != draft_url:
            delete_portrait_file(original_url)
        await db.refresh(outfit)
    return outfit


async def confirm_outfit(
    db: AsyncSession,
    user_id: int,
    outfit_id: int,
) -> CompanionOutfit:
    """确认草稿（failed 状态可重试切分，立绘已转正不再走 temp-media）：先转正
    （temp-media → companion-avatars，管线读永久路径）再以不停用现有激活行的方式插入
    2d 行并启动切分；描述生成后台进行，不阻塞就绪。"""
    async with get_avatar_job_lock(user_id):
        outfit = await _get_outfit(db, user_id, outfit_id)
        if outfit is None:
            raise OutfitNotFoundError(f"outfit {outfit_id} not found")
        if outfit.status not in ("draft", "failed"):
            raise OutfitStateError("仅草稿或失败状态可以确认")
        if await _has_splitting(db, user_id):
            raise OutfitStateError("有一套外观正在生成中，请稍候")
        avatar = await _active_avatar(db, user_id)
        if avatar is None:
            raise OutfitStateError("找不到激活头像行")
        if outfit.fullbody_url.startswith("temp-media/"):
            moved = await _read_temp_media_bytes(outfit.fullbody_url)
            if moved is None:
                raise OutfitDraftExpiredError("外观草稿已过期，请重新生成")
            outfit.fullbody_url, _, _ = await _persist_portrait_bytes(
                moved[0],
                moved[1],
            )
        outfit.status = "splitting"
        outfit.pending_wear = True
        model = Companion2DModel(
            user_id=user_id,
            avatar_id=avatar.id,
            outfit_id=outfit.id,
            status="generating",
        )
        db.add(model)
        await db.commit()
        await db.refresh(model)
        await db.refresh(outfit)

    run_mesh2d_pipeline(
        user_id=user_id,
        model_id=model.id,
        fullbody_url=outfit.fullbody_url,
        priority="high",
    )
    _kick_describe(user_id, outfit.id)
    return outfit


async def resume_outfit_split(user_id: int, outfit_id: int) -> bool:
    """从同一 outfit/model 行恢复被进程重启打断的切分，不创建新外观或新模型行。"""
    async with SESSION_LOCAL() as db:
        outfit = await _get_outfit(db, user_id, outfit_id)
        if outfit is None or outfit.status != "splitting":
            return False
        model = (
            await db.execute(
                select(Companion2DModel)
                .where(
                    Companion2DModel.user_id == user_id,
                    Companion2DModel.outfit_id == outfit_id,
                    Companion2DModel.status == "generating",
                )
                .order_by(Companion2DModel.id.desc())
                .limit(1),
            )
        ).scalar_one_or_none()
        if model is None:
            return False
        if model.id in active_model_ids():
            return True
        model_id = model.id
        fullbody_url = outfit.fullbody_url
        run_mesh2d_pipeline(
            user_id=user_id,
            model_id=model_id,
            fullbody_url=fullbody_url,
            priority="high",
        )
    return True


async def _ready_outfit_for_pose(db: AsyncSession, user_id: int, outfit_id: int) -> CompanionOutfit:
    """单侧姿态重绘、采纳与提示词共用的就绪外观守卫；返回外观行。"""
    outfit = await _get_outfit(db, user_id, outfit_id)
    if outfit is None:
        raise OutfitNotFoundError(f"outfit {outfit_id} not found")
    if outfit.status != "ready":
        raise OutfitStateError("仅切分成功的外观可以重新生成扶边姿态")
    if await _has_splitting(db, user_id):
        raise OutfitStateError("有一套外观正在生成中，请稍候")
    model_id = await db.scalar(
        select(Companion2DModel.id)
        .where(
            Companion2DModel.user_id == user_id,
            Companion2DModel.outfit_id == outfit.id,
            Companion2DModel.status == "succeeded",
        )
        .order_by(Companion2DModel.id.desc())
        .limit(1),
    )
    if model_id is None:
        raise OutfitStateError("外观缺少 2D 资产，请重新生成外观")
    return outfit


async def regenerate_outfit_pose(
    db: AsyncSession,
    user_id: int,
    outfit_id: int,
    side: Literal["left", "right"],
) -> CompanionOutfit:
    """单侧重生成一侧扶边姿态：只替换该侧两张姿态纹理与 manifest poses 子树，
    PSD 与另一侧不动；生成失败时旧姿态保持可用、外观仍为 ready。与整包切分互斥，
    同一外观同一时间只允许一个单侧任务（单飞标记在管线模块）。"""
    async with get_avatar_job_lock(user_id):
        outfit = await _ready_outfit_for_pose(db, user_id, outfit_id)
        if pose_regeneration_in_progress(user_id, outfit.id):
            raise OutfitStateError("扶边姿态正在重新生成中，请稍候")

    # 锁内校验到锁外提交之间无挂起点，单飞检查与在飞标记登记在同一事件轮内原子完成
    run_pose_side_regeneration(user_id=user_id, outfit_id=outfit.id, side=side)
    return outfit


async def prepare_outfit_prompt(
    db: AsyncSession,
    user_id: int,
    *,
    description: str | None,
    image: bytes | None = None,
    content_type: str | None = None,
) -> str:
    """自备图提示词（创建语境）：守卫与参考图整合链同创建草稿（整合失败降级纯文字着装描述，
    身份仍由全身种子图锚定）；不创建草稿行，不做生图。种子缺失按 AI 路径同一文案失败。"""
    effective_description = (description or "").strip()
    if not effective_description and image is None:
        raise OutfitError("请先描述想要的着装，或上传一张参考图")

    (
        avatar,
        _mesh2d,
        species,
        appearance,
        personality,
        style,
        rig_type,
    ) = await _outfit_generation_context(db, user_id)
    await _require_fullbody_seed_readable(avatar)
    # 结束读事务：整合往返期间不占连接（短会话纪律）
    await db.commit()

    garment_text: str | None = None
    if image is not None:
        garment_text = await _describe_reference_garment(
            user_id,
            {},
            image,
            content_type,
            requirement=effective_description,
        )
    feedback = garment_text or effective_description or "为角色设计一套新的着装"
    return build_outfit_prompt(
        template=resolve_fullbody_template(species, rig_type, style),
        style_id=style,
        feedback=feedback,
        appearance=appearance,
        personality=personality,
        identity_anchor="reference-self-source",
        canvas_aspect=_fullbody_aspect_for(rig_type),
    )


async def prepare_outfit_regenerate_prompt(
    db: AsyncSession,
    user_id: int,
    outfit_id: int,
    *,
    feedback: str | None,
) -> str:
    """自备图提示词（草稿重绘语境）：守卫与反馈整合同草稿重绘（含设计稿补整合）；
    身份恒由全身种子图锚定，种子缺失按 AI 路径同一文案失败。"""
    outfit = await _get_outfit(db, user_id, outfit_id)
    if outfit is None:
        raise OutfitNotFoundError(f"outfit {outfit_id} not found")
    if outfit.status not in ("draft", "failed"):
        raise OutfitStateError("仅草稿或失败状态可以微调重绘")
    effective_feedback = (feedback or "").strip()

    (
        avatar,
        _mesh2d,
        species,
        appearance,
        personality,
        style,
        rig_type,
    ) = await _outfit_generation_context(db, user_id)
    await _require_fullbody_seed_readable(avatar)
    source = safe_json_loads(outfit.source_json or "{}", default={})
    if not isinstance(source, dict):
        source = {}
    await db.commit()

    description = str(source.get("description") or "").strip()
    garment_text = str(source.get("reference_description") or "").strip()
    if not garment_text and source.get("reference_image_path"):
        garment_text = await _describe_reference_garment(user_id, source, requirement=description)
    combined_feedback = "；".join(part for part in (garment_text or description, effective_feedback) if part)
    if not combined_feedback:
        raise OutfitError("请先描述想要的着装或修改要求")
    return build_outfit_prompt(
        template=resolve_fullbody_template(species, rig_type, style),
        style_id=style,
        feedback=combined_feedback,
        appearance=appearance,
        personality=personality,
        identity_anchor="reference-self-source",
        canvas_aspect=_fullbody_aspect_for(rig_type),
    )


async def adopt_outfit_draft_image(
    db: AsyncSession,
    user_id: int,
    *,
    description: str | None,
    data: bytes,
    content_type: str | None,
) -> CompanionOutfit:
    """自备图采纳（创建语境）：用户外部生成的立绘按创建草稿语义入库（temp-media 草稿，确认后转正）。"""
    (
        _avatar,
        _mesh2d,
        _species,
        _appearance,
        _personality,
        style,
        _rig_type,
    ) = await _outfit_generation_context(db, user_id)
    effective_description = (description or "").strip()
    fullbody_url, _, _ = await _persist_portrait_or_draft(
        data,
        user_id,
        content_type or "image/png",
        persist=False,
    )

    async with get_avatar_job_lock(user_id):
        outfit = CompanionOutfit(
            user_id=user_id,
            name="新外观",
            fullbody_url=fullbody_url,
            style=style,
            status="draft",
            source_json=json.dumps({"description": effective_description}, ensure_ascii=False),
        )
        db.add(outfit)
        await db.commit()
        await db.refresh(outfit)
    return outfit


async def adopt_outfit_regenerate_image(
    db: AsyncSession,
    user_id: int,
    outfit_id: int,
    *,
    data: bytes,
    content_type: str | None,
) -> CompanionOutfit:
    """自备图采纳（草稿重绘语境）：替换草稿/失败外观的立绘，状态回到草稿；成功发 outfit.updated。"""
    outfit = await _get_outfit(db, user_id, outfit_id)
    if outfit is None:
        raise OutfitNotFoundError(f"outfit {outfit_id} not found")
    if outfit.status not in ("draft", "failed"):
        raise OutfitStateError("仅草稿或失败状态可以微调重绘")
    original_url = outfit.fullbody_url

    fullbody_url, _, _ = await _persist_portrait_or_draft(
        data,
        user_id,
        content_type or "image/png",
        persist=False,
    )

    async with get_avatar_job_lock(user_id):
        # 上传期间外观可能已被确认或另一次重绘替换，锁内刷新后按原始版本核对（同 regenerate_outfit_draft）
        outfit = await _get_outfit(db, user_id, outfit_id)
        if outfit is None or outfit.status not in ("draft", "failed") or outfit.fullbody_url != original_url:
            delete_portrait_file(fullbody_url)
            raise OutfitStateError("外观已发生变化，请刷新后重试")
        outfit.fullbody_url = fullbody_url
        outfit.status = "draft"
        outfit.pending_wear = False
        emit_ws_event(
            db,
            user_id=user_id,
            event_type="companion.outfit.updated",
            payload={"outfit_id": outfit.id, "worn": False},
        )
        await db.commit()
        if original_url != fullbody_url:
            delete_portrait_file(original_url)
        await db.refresh(outfit)
    return outfit


async def prepare_pose_prompt(
    db: AsyncSession,
    user_id: int,
    outfit_id: int,
    side: Literal["left", "right"],
) -> str:
    """自备图提示词（单侧扶边姿态）：以当前外观正面立绘为参考图锚定身份与穿着，
    姿势/构图规范与生成链一致。立绘缺失或不可读时按状态冲突失败（提示词必须带得上参考图）；
    立绘可读时附带按主色推荐的纯色背景，色幕分析失败只降级为通用纯色要求。"""
    outfit = await _ready_outfit_for_pose(db, user_id, outfit_id)
    if (
        not outfit.fullbody_url
        or await asyncio.to_thread(
            load_avatar_bytes_as_data_uri,
            outfit.fullbody_url,
        )
        is None
    ):
        raise OutfitStateError("外观立绘缺失或无法读取，请先重新生成外观")
    persona = await get_or_create_persona(db, user_id)
    definition = load_persona_definition(persona)
    backdrop: str | None = None
    resolved = resolve_uploaded_avatar_path(outfit.fullbody_url.rsplit("/", 1)[-1])
    if resolved is not None:
        try:
            artwork = await asyncio.to_thread(resolved[0].read_bytes)
            backdrop = await asyncio.to_thread(pose_backdrop_for_artwork, artwork)
        except Exception:
            backdrop = None
    return build_pose_side_prompt(
        side,
        appearance=str(definition.get("appearance") or "").strip(),
        outfit_description=(outfit.description or "").strip(),
        backdrop=backdrop,
    )


async def adopt_outfit_pose(
    db: AsyncSession,
    user_id: int,
    outfit_id: int,
    side: Literal["left", "right"],
    *,
    data: bytes,
) -> CompanionOutfit:
    """自备图采纳（单侧姿态）：校验后就地入队既有单侧管线（跳过主图生图，抠图/定位/闭眼帧仍由后端完成），
    完成与失败经 WS 事件驱动刷新，语义与单侧重绘一致。"""
    async with get_avatar_job_lock(user_id):
        outfit = await _ready_outfit_for_pose(db, user_id, outfit_id)
        if pose_regeneration_in_progress(user_id, outfit.id):
            raise OutfitStateError("扶边姿态正在重新生成中，请稍候")

    # 锁内校验到锁外提交之间无挂起点，单飞检查与在飞标记登记在同一事件轮内原子完成
    run_pose_side_regeneration(user_id=user_id, outfit_id=outfit.id, side=side, user_image=data)
    return outfit


async def activate_outfit(
    db: AsyncSession,
    user_id: int,
    outfit_id: int,
) -> CompanionOutfit:
    """即时穿着就绪外观；同事务清空全部自动穿着标记，防止在途切分完成后覆盖用户手选。"""
    async with get_avatar_job_lock(user_id):
        outfit = await _get_outfit(db, user_id, outfit_id)
        if outfit is None:
            raise OutfitNotFoundError(f"outfit {outfit_id} not found")
        if outfit.status != "ready":
            raise OutfitStateError("外观尚未就绪，无法穿着")
        model = (
            await db.execute(
                select(Companion2DModel)
                .where(
                    Companion2DModel.outfit_id == outfit.id,
                    Companion2DModel.status == "succeeded",
                )
                .order_by(Companion2DModel.id.desc())
                .limit(1),
            )
        ).scalar_one_or_none()
        if model is None:
            raise OutfitStateError("外观缺少可穿的 2D 资产，请重新生成")

        # 先停用后激活：部分唯一索引不可延迟，顺序颠倒会在中间态撞唯一约束
        await db.execute(
            update(CompanionOutfit)
            .where(
                CompanionOutfit.user_id == user_id,
                CompanionOutfit.pending_wear.is_(True),
            )
            .values(pending_wear=False)
            .execution_options(synchronize_session=False),
        )
        await db.execute(
            update(Companion2DModel)
            .where(
                Companion2DModel.user_id == user_id,
                Companion2DModel.active.is_(True),
            )
            .values(active=False)
            .execution_options(synchronize_session=False),
        )
        await db.execute(
            update(CompanionOutfit)
            .where(CompanionOutfit.user_id == user_id, CompanionOutfit.active.is_(True))
            .values(active=False)
            .execution_options(synchronize_session=False),
        )
        model.active = True
        outfit.active = True
        emit_ws_event(
            db,
            user_id=user_id,
            event_type="companion.outfit.updated",
            payload={"outfit_id": outfit.id, "worn": True},
        )
        await db.commit()
        await db.refresh(outfit)
    return outfit


def _unlink_companion_asset(user_id: int, storage_path: str | None) -> None:
    if not storage_path:
        return
    filename = storage_path.replace("\\", "/").rsplit("/", 1)[-1].split("?")[0]
    resolved = resolve_companion_asset_path(user_id, filename)
    if resolved is not None:
        with contextlib.suppress(OSError):
            resolved[0].unlink(missing_ok=True)


async def delete_outfit(db: AsyncSession, user_id: int, outfit_id: int) -> None:
    """删除非穿着、非切分中的外观（含初始形象）；2d 行与产物文件 best-effort 清理。
    初始形象的立绘文件即头像行的正面种子，归头像所有——外观删除不得带走它，
    否则后续换装生成都会因身份参考丢失而失败。"""
    async with get_avatar_job_lock(user_id):
        outfit = await _get_outfit(db, user_id, outfit_id)
        if outfit is None:
            raise OutfitNotFoundError(f"outfit {outfit_id} not found")
        if outfit.active:
            raise OutfitStateError("穿着中的外观不能删除，请先切换到其他外观")
        if outfit.status == "splitting":
            raise OutfitStateError("正在生成中的外观不能删除")

        avatar = await _active_avatar(db, user_id)
        avatar_files = {avatar.seed_front_2d_url, avatar.asset_url} if avatar is not None else set()
        models = (
            (
                await db.execute(
                    select(Companion2DModel).where(
                        Companion2DModel.outfit_id == outfit.id,
                    ),
                )
            )
            .scalars()
            .all()
        )
        for model in models:
            _unlink_companion_asset(user_id, model.manifest_path)
            for entry in safe_json_loads(model.layers_json or "[]", default=[]):
                if isinstance(entry, dict) and entry.get("url"):
                    _unlink_companion_asset(user_id, str(entry["url"]))
        await db.execute(
            delete(Companion2DModel).where(Companion2DModel.outfit_id == outfit.id),
        )
        _delete_reference_file(outfit)
        if outfit.fullbody_url not in avatar_files:
            delete_portrait_file(outfit.fullbody_url)
        emit_ws_event(
            db,
            user_id=user_id,
            event_type="companion.outfit.updated",
            payload={"outfit_id": outfit.id, "worn": False},
        )
        await db.delete(outfit)
        await db.commit()


def _kick_describe(user_id: int, outfit_id: int) -> None:
    task = asyncio.create_task(
        _describe_outfit(user_id, outfit_id),
        name=f"companion.outfit.describe.{user_id}.{outfit_id}",
    )
    _DESCRIBE_TASKS.add(task)
    task.add_done_callback(_DESCRIBE_TASKS.discard)
    track_user_task(user_id, task)


async def _describe_outfit(user_id: int, outfit_id: int) -> None:
    """后台生成着装描述；读 → LLM（无会话）→ 写三段各自短会话，失败只记日志不阻塞就绪。"""
    try:
        async with SESSION_LOCAL() as db:
            persona = await get_or_create_persona(db, user_id)
            outfit = await _get_outfit(db, user_id, outfit_id)
            if outfit is None:
                return
            definition = safe_json_loads(persona.definition_json or "{}", default={})
            source = safe_json_loads(outfit.source_json or "{}", default={})
            language_value = await db.scalar(
                select(UserSetting.setting_value).where(
                    UserSetting.user_id == user_id,
                    UserSetting.setting_key == "language",
                ),
            )
            payload = {
                "output_language": resolve_language(language_value or DEFAULT_LANGUAGE),
                "appearance": str(definition.get("appearance") or "") if isinstance(definition, dict) else "",
                "personality": str(definition.get("personality") or "") if isinstance(definition, dict) else "",
                # 设计稿已整合原始文字要求，优先取用；只有文字要求时用原话，命名不关心其来源
                "outfit_request": str(source.get("reference_description") or source.get("description") or "")
                if isinstance(source, dict)
                else "",
                "style": outfit.style,
            }
        raw = await chat(
            None,
            user_id,
            OUTFIT_DESCRIBE_SYSTEM,
            json.dumps(payload, ensure_ascii=False),
        )
        parsed = parse_llm_json(raw) or {}
        if not isinstance(parsed, dict):
            return
        name = str(parsed.get("name") or "").strip()[:64]
        description = str(parsed.get("description") or "").strip()
        if not name and not description:
            return
        async with SESSION_LOCAL() as db:
            outfit = await _get_outfit(db, user_id, outfit_id)
            if outfit is None:
                return
            if name:
                outfit.name = name
            if description:
                outfit.description = description
            await db.commit()
    except Exception:
        logger.warning(
            "outfit description generation failed",
            extra={"user_id": user_id, "outfit_id": outfit_id},
            exc_info=True,
        )
