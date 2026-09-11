"""Outfit service —— 2D 换装外观生命周期：草稿生成 → 确认转正切分 → 穿着 / 删除。

服装 / 发型是可换元素而非身份变更（DESIGN §5.4 形象锁定的豁免，同背面种子先例）：
身份锚点恒为激活头像行的头像种子图（避免派生图迭代失真），本服务不检查 raise_if_image_sealed。
两段式激活不变量：切分完成前旧 2d 行保持激活，翻转只发生在 2d 管线的成功
接缝（2d pipeline.py）；提前翻转会令 get_active_mesh2d_response 落空、精灵掉蛋。
所有外观状态校验与翻转（含管线接缝）共用用户级锁——锁外校验会让并发双击确认
插出两行切分任务、或令在途切分覆盖用户手选。
"""

import asyncio
import base64
import contextlib
import json
import re
from datetime import timedelta

from components import (
    DEFAULT_LANGUAGE,
    SESSION_LOCAL,
    SETTINGS,
    get_logger,
    resolve_prompt_text,
    safe_json_loads,
    utc_now,
)
from components.user_maintenance_runtime import track_user_task
from modules.companion import (
    OUTFIT_POLICY_DEFAULT,
    AvatarAsset,
    Companion2DModel,
    CompanionOutfit,
    OutfitResponse,
    Persona,
)
from modules.ws import emit_ws_event
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..llm import build_outfit_prompt, chat, resolve_fullbody_template
from .asset_store import build_data_uri, resolve_companion_asset_path
from .avatar_service import (
    _fullbody_size_for,
    _generate_one_portrait_with_moderation_retry,
    _persist_portrait_bytes,
    _read_temp_media_bytes,
    _resolve_fullbody_rig_type,
    delete_portrait_file,
    get_avatar_job_lock,
    load_avatar_bytes_as_data_uri,
    resolve_uploaded_avatar_path,
)
from .mesh2d import active_model_ids, mesh2d_response, run_mesh2d_pipeline
from .persona_service import get_or_create_persona, load_persona_definition
from .response_builders import outfit_response
from .room_backdrop_service import invalidate_room_for_outfit

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
            select(CompanionOutfit).where(
                CompanionOutfit.id == outfit_id,
                CompanionOutfit.user_id == user_id,
            ),
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
        style=mesh2d.style or "cel_shading",
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
        or "cel_shading"
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


def _reference_data_uri(source: dict) -> str | None:
    """把草稿期保存的参考图读回 data URI；文件丢失时返回 None（静默降级为纯文本重绘）。"""
    ref_path = source.get("reference_image_path")
    if not isinstance(ref_path, str) or not ref_path:
        return None
    filename = ref_path.rsplit("/", 1)[-1]
    resolved = resolve_uploaded_avatar_path(filename)
    if resolved is None:
        return None
    path, content_type = resolved
    with contextlib.suppress(OSError):
        return build_data_uri(path.read_bytes(), content_type)
    return None


async def _generate_outfit_fullbody(
    user_id: int,
    *,
    species: str,
    rig_type: str,
    style: str,
    appearance: str,
    personality: str,
    feedback: str,
    identity_uri: str,
    secondary_uri: str | None,
) -> str:
    """生成换装全身立绘草稿（persist=False 落 temp-media）；返回裸路径。画幅与姿态模板随 rig_type 分桶。"""
    prompt = build_outfit_prompt(
        template=resolve_fullbody_template(species, rig_type, style),
        style_id=style,
        feedback=feedback,
        appearance=appearance,
        personality=personality,
    )
    draft_url, _, _, _ = await _generate_one_portrait_with_moderation_retry(
        prompt,
        user_id,
        reference_image=identity_uri,
        secondary_reference_image=secondary_uri,
        size=_fullbody_size_for(rig_type),
        persist=False,
        preferred_provider=SETTINGS.companion_asset_image_providers,
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
    """文本描述 + 可选参考图创建外观草稿；身份参考恒为激活头像的正面种子（主），用户图为次参考。"""
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
    identity_uri = load_avatar_bytes_as_data_uri(avatar.asset_url)
    if identity_uri is None:
        raise OutfitError("头像种子图读取失败，请稍后重试")
    # 结束读事务：生图往返期间不占连接（短会话纪律）
    await db.commit()

    source: dict = {"description": effective_description}
    secondary_uri = None
    if image is not None:
        # 参考图立即转存 companion-avatars（temp-media 会过期，微调重绘还要复用）
        ref_path, _, _ = await _persist_portrait_bytes(
            image,
            content_type or "image/png",
        )
        source["reference_image_path"] = ref_path
        secondary_uri = f"data:{content_type or 'image/png'};base64,{base64.b64encode(image).decode('ascii')}"

    feedback = effective_description or "参考第二张图中的服装与发型，为角色设计一套新的着装"
    draft_url = await _generate_outfit_fullbody(
        user_id,
        species=species,
        rig_type=rig_type,
        style=style,
        appearance=appearance,
        personality=personality,
        feedback=feedback,
        identity_uri=identity_uri,
        secondary_uri=secondary_uri,
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
) -> CompanionOutfit:
    """草稿微调重绘：身份锚点与参考图不变，feedback 并入着装要求。"""
    outfit = await _get_outfit(db, user_id, outfit_id)
    if outfit is None:
        raise OutfitNotFoundError(f"outfit {outfit_id} not found")
    if outfit.status != "draft":
        raise OutfitStateError("仅草稿状态可以微调重绘")

    (
        avatar,
        _,
        species,
        appearance,
        personality,
        style,
        rig_type,
    ) = await _outfit_generation_context(db, user_id)
    identity_uri = load_avatar_bytes_as_data_uri(avatar.asset_url)
    if identity_uri is None:
        raise OutfitError("头像种子图读取失败，请稍后重试")
    await db.commit()

    source = safe_json_loads(outfit.source_json or "{}", default={})
    if not isinstance(source, dict):
        source = {}
    secondary_uri = _reference_data_uri(source)
    description = str(source.get("description") or "").strip()
    effective_feedback = "；".join(part for part in (description, (feedback or "").strip()) if part)

    draft_url = await _generate_outfit_fullbody(
        user_id,
        species=species,
        rig_type=rig_type,
        style=style,
        appearance=appearance,
        personality=personality,
        feedback=effective_feedback,
        identity_uri=identity_uri,
        secondary_uri=secondary_uri,
    )

    async with get_avatar_job_lock(user_id):
        # 生图往返期间行可能已被确认（切分中）——锁内重读校验，防止把已转正的立绘路径覆盖回 temp-media
        outfit = await _get_outfit(db, user_id, outfit_id)
        if outfit is None or outfit.status != "draft":
            raise OutfitStateError("仅草稿状态可以微调重绘")
        outfit.fullbody_url = draft_url
        if (feedback or "").strip():
            source["feedback"] = feedback.strip()
        outfit.source_json = json.dumps(source, ensure_ascii=False)
        await db.commit()
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
            moved = _read_temp_media_bytes(outfit.fullbody_url)
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
            priority="high",
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
    # 换装成功 → 让当前 active 房间图失效并 schedule origin=outfit 重建
    try:
        await invalidate_room_for_outfit(user_id, new_fingerprint=str(outfit.id))
    except Exception:
        logger.warning(
            "outfit activate -> room invalidation failed",
            extra={"user_id": user_id, "outfit_id": outfit.id},
            exc_info=True,
        )
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


_DESCRIBE_SYSTEM = (
    "你是桌面伙伴的着装描述撰写助手。根据角色设定与用户的着装要求，为这套外观撰写名称与描述，"
    "供衣柜展示与伙伴在对话中认知自己的穿着。\n"
    "只输出一个 JSON 对象（不要 markdown 代码块）："
    '{"name": "不超过 8 个字的外观名称", "description": "2-3 句中文描述，涵盖服装风格、配色与材质、'
    '发型与配饰变化、整体气质与适合场合"}'
)


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
            payload = {
                "appearance": str(definition.get("appearance") or "") if isinstance(definition, dict) else "",
                "personality": str(definition.get("personality") or "") if isinstance(definition, dict) else "",
                "outfit_request": str(source.get("description") or "按用户参考图设计")
                if isinstance(source, dict)
                else "按用户参考图设计",
                "style": outfit.style,
            }
        raw = await chat(
            None,
            user_id,
            _DESCRIBE_SYSTEM,
            json.dumps(payload, ensure_ascii=False),
        )
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        parsed = json.loads(match.group(0)) if match else {}
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


# 双语着装块标题。
_OUTFIT_LABELS_TEXTS: dict[str, str] = {
    "zh": "# 当前着装",
    "en": "# Current outfit",
}

# 双语着装块 fallback：用户尚未穿任何 outfit 时使用，确保 caller 拿到的 outfit_block 非空。
_DEFAULT_OUTFIT_TEXTS: dict[str, str] = {
    "zh": "当前着装：（默认形象，尚未换装）",
    "en": "Current outfit: (default appearance, no outfit set)",
}


async def build_outfit_extras(
    db: AsyncSession,
    user_id: int,
    *,
    language: str = DEFAULT_LANGUAGE,
) -> str:
    """当前穿着的着装描述，注入系统提示词稳定段——伙伴自知穿着，为着装联动打底。

    无 outfit 时返回双语 fallback，caller 不必再包一层默认文案。
    """
    outfit = (
        await db.execute(
            select(CompanionOutfit).where(
                CompanionOutfit.user_id == user_id,
                CompanionOutfit.active.is_(True),
                CompanionOutfit.status == "ready",
            ),
        )
    ).scalar_one_or_none()
    if outfit is None or not (outfit.description or "").strip():
        return resolve_prompt_text(_DEFAULT_OUTFIT_TEXTS, language)
    label = resolve_prompt_text(_OUTFIT_LABELS_TEXTS, language)
    return f"{label}\n{outfit.name}:{outfit.description.strip()[:600]}"
