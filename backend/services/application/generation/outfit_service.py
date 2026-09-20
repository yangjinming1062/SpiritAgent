"""衣橱草稿、确认、穿着与删除；共享事务约束见本模块 README。"""

import asyncio
import base64
import json
from datetime import timedelta

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
    CompanionOutfit,
    ImageReviseMode,
    OutfitResponse,
    Persona,
)
from modules.settings import UserSetting
from modules.ws import emit_ws_event
from prompts.generation import EDIT_PRESERVE_OUTFIT, OUTFIT_DESCRIBE_SYSTEM
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from services.domains.companion import get_or_create_persona, load_persona_definition
from services.infrastructure.llm import (
    build_image_edit_prompt,
    build_outfit_prompt,
    chat,
    describe_garment_image,
)

from .avatar_service import (
    _FULLBODY_ASPECT,
    _FULLBODY_SIZE,
    _generate_one_portrait_with_moderation_retry,
    _persist_portrait_bytes,
    _persist_portrait_or_draft,
    _read_temp_media_bytes,
    delete_portrait_file,
    get_avatar_job_lock,
    load_avatar_bytes_as_data_uri,
    resolve_uploaded_avatar_path,
)
from .response_builders import outfit_response

logger = get_logger(__name__)

# temp-media 草稿 24h TTL，留 1h 余量在读取时清扫过期草稿
_DRAFT_TTL = timedelta(hours=23)
_DESCRIBE_TASKS: dict[tuple[int, int], asyncio.Task[None]] = {}


class OutfitError(RuntimeError):
    """换装流程错误；str(exc) 恒为可展示的公开文案。"""


class OutfitNotFoundError(OutfitError):
    """目标外观行不存在或不属于调用者。"""


class OutfitStateError(OutfitError):
    """状态守卫拒绝（非法状态转换 / 删除保护）。"""


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


async def _sweep_stale(db: AsyncSession, user_id: int) -> None:
    """读取时顺带清扫：过期草稿置 expired（参考图上传文件一并清理）。"""
    now = utc_now()
    rows = (
        (
            await db.execute(
                select(CompanionOutfit).where(
                    CompanionOutfit.user_id == user_id,
                    CompanionOutfit.status == "draft",
                ),
            )
        )
        .scalars()
        .all()
    )
    changed = False
    for outfit in rows:
        if now - outfit.updated_at > _DRAFT_TTL:
            outfit.status = "expired"
            _delete_reference_file(outfit)
            changed = True
    if changed:
        await db.commit()


def _delete_reference_file(outfit: CompanionOutfit) -> None:
    source = safe_json_loads(outfit.source_json or "{}", default={})
    ref_path = source.get("reference_image_path") if isinstance(source, dict) else None
    if isinstance(ref_path, str) and ref_path:
        delete_portrait_file(ref_path)


async def list_outfits(db: AsyncSession, user_id: int) -> list[OutfitResponse]:
    async with get_avatar_job_lock(user_id):
        await _sweep_stale(db, user_id)
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
        return [outfit_response(outfit) for outfit in outfits]


async def _outfit_generation_context(
    db: AsyncSession,
    user_id: int,
) -> tuple[AvatarAsset, str, str, str]:
    """返回 (激活头像, 物种, 外貌, 性格)；守卫失败抛 OutfitStateError。"""
    avatar = await _active_avatar(db, user_id)
    if avatar is None or not avatar.is_fullbody_confirmed:
        raise OutfitStateError("请先确认全身种子图")
    persona = await get_or_create_persona(db, user_id)
    if not persona.is_complete:
        raise OutfitStateError("请先完成 onboarding 再设计外观")
    definition = load_persona_definition(persona)
    species = str(definition.get("biological_type") or "").strip()
    return (
        avatar,
        species,
        str(definition.get("appearance") or "").strip(),
        str(definition.get("personality") or "").strip(),
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
    prompt: str,
    reference_image: str,
    image_edit: bool = False,
) -> str:
    draft_url, _, _, _ = await _generate_one_portrait_with_moderation_retry(
        prompt,
        user_id,
        reference_image=reference_image,
        size=_FULLBODY_SIZE,
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
    """根据文字与可选服装参考图创建外观草稿。"""
    effective_description = (description or "").strip()
    if not effective_description and image is None:
        raise OutfitError("请先描述想要的着装，或上传一张参考图")

    avatar, species, appearance, personality = await _outfit_generation_context(db, user_id)
    identity_uri = await _require_fullbody_seed_readable(avatar)
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

    prompt = await build_outfit_prompt(
        user_id=user_id,
        reference_image=identity_uri,
        species=species,
        appearance=appearance,
        personality=personality,
        feedback=feedback,
    )
    draft_url = await _generate_outfit_fullbody(
        user_id,
        prompt=prompt,
        reference_image=identity_uri,
    )

    async with get_avatar_job_lock(user_id):
        outfit = CompanionOutfit(
            user_id=user_id,
            name="新外观",
            fullbody_url=draft_url,
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
    """微调或重新生成外观；成功后回到草稿，等待用户确认。"""
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

    avatar, species, appearance, personality = await _outfit_generation_context(db, user_id)

    source = safe_json_loads(outfit.source_json or "{}", default={})
    if not isinstance(source, dict):
        source = {}
    await db.commit()

    if mode == "edit":
        # 编辑底图即上一版草稿立绘；身份已在其中，不重锚种子图（preserve 条款约束五官/身材不变）。
        reference_uri = await asyncio.to_thread(load_avatar_bytes_as_data_uri, original_url)
        if not reference_uri:
            raise OutfitDraftExpiredError("上一版草稿已过期或无法读取，请改用重新生成")
        prompt = build_image_edit_prompt(effective_feedback, preserve=EDIT_PRESERVE_OUTFIT)
    else:
        reference_uri = await _require_fullbody_seed_readable(avatar)
        description = str(source.get("description") or "").strip()
        garment_text = str(source.get("reference_description") or "").strip()
        if not garment_text and source.get("reference_image_path"):
            # 旧草稿或上次整合失败：重新生成时补一次整合，成功则随本次 source_json 持久化
            garment_text = await _describe_reference_garment(user_id, source, requirement=description)
        # 着装描述恒为一段完整文本（设计稿已整合原始文字要求），再叠加本次修改要求
        combined_feedback = "；".join(part for part in (garment_text or description, effective_feedback) if part)
        prompt = await build_outfit_prompt(
            user_id=user_id,
            reference_image=reference_uri,
            species=species,
            feedback=combined_feedback,
            appearance=appearance,
            personality=personality,
        )

    draft_url = await _generate_outfit_fullbody(
        user_id,
        prompt=prompt,
        reference_image=reference_uri,
        image_edit=mode == "edit",
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
    """确认草稿（failed 可重试确认）：立绘转正为持久参考图，状态到 ready（参考图就绪）。
    确认不触发任何生成，也不自动穿着；描述生成后台进行，穿着由 activate_outfit 显式完成。"""
    async with get_avatar_job_lock(user_id):
        outfit = await _get_outfit(db, user_id, outfit_id)
        if outfit is None:
            raise OutfitNotFoundError(f"outfit {outfit_id} not found")
        if outfit.status not in ("draft", "failed"):
            raise OutfitStateError("仅草稿或失败状态可以确认")
        if outfit.fullbody_url.startswith("temp-media/"):
            moved = await _read_temp_media_bytes(outfit.fullbody_url)
            if moved is None:
                raise OutfitDraftExpiredError("外观草稿已过期，请重新生成")
            outfit.fullbody_url, _, _ = await _persist_portrait_bytes(
                moved[0],
                moved[1],
            )
        outfit.status = "ready"
        await db.commit()
        await db.refresh(outfit)

    schedule_outfit_description(user_id, outfit.id)
    return outfit


async def prepare_outfit_prompt(
    db: AsyncSession,
    user_id: int,
    *,
    description: str | None,
    image: bytes | None = None,
    content_type: str | None = None,
) -> str:
    """根据新装设计生成外部制作提示词，不创建草稿。"""
    effective_description = (description or "").strip()
    if not effective_description and image is None:
        raise OutfitError("请先描述想要的着装，或上传一张参考图")

    avatar, species, appearance, personality = await _outfit_generation_context(db, user_id)
    identity_uri = await _require_fullbody_seed_readable(avatar)
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
    return await build_outfit_prompt(
        user_id=user_id,
        reference_image=identity_uri,
        species=species,
        feedback=feedback,
        appearance=appearance,
        personality=personality,
        canvas_aspect=_FULLBODY_ASPECT,
    )


async def prepare_outfit_regenerate_prompt(
    db: AsyncSession,
    user_id: int,
    outfit_id: int,
    *,
    feedback: str | None,
) -> str:
    """根据已有草稿设计与反馈生成外部制作提示词。"""
    outfit = await _get_outfit(db, user_id, outfit_id)
    if outfit is None:
        raise OutfitNotFoundError(f"outfit {outfit_id} not found")
    if outfit.status not in ("draft", "failed"):
        raise OutfitStateError("仅草稿或失败状态可以微调重绘")
    effective_feedback = (feedback or "").strip()

    avatar, species, appearance, personality = await _outfit_generation_context(db, user_id)
    identity_uri = await _require_fullbody_seed_readable(avatar)
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
    return await build_outfit_prompt(
        user_id=user_id,
        reference_image=identity_uri,
        species=species,
        feedback=combined_feedback,
        appearance=appearance,
        personality=personality,
        canvas_aspect=_FULLBODY_ASPECT,
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
    await _outfit_generation_context(db, user_id)
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


async def activate_outfit(
    db: AsyncSession,
    user_id: int,
    outfit_id: int,
) -> CompanionOutfit:
    """即时穿着就绪外观：翻转该用户的激活外观。`active` 是当前生效着装描述的唯一权威，
    供房间 / 出镜媒体生成消费；对渲染形象的影响由各形象链自行处理。"""
    async with get_avatar_job_lock(user_id):
        outfit = await _get_outfit(db, user_id, outfit_id)
        if outfit is None:
            raise OutfitNotFoundError(f"outfit {outfit_id} not found")
        if outfit.status != "ready":
            raise OutfitStateError("外观尚未确认，无法穿着")

        await db.execute(
            update(CompanionOutfit)
            .where(CompanionOutfit.user_id == user_id, CompanionOutfit.active.is_(True))
            .values(active=False)
            .execution_options(synchronize_session=False),
        )
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


async def delete_outfit(db: AsyncSession, user_id: int, outfit_id: int) -> None:
    """删除非穿着外观；清理其独立立绘与上传的着装参考图。"""
    async with get_avatar_job_lock(user_id):
        outfit = await _get_outfit(db, user_id, outfit_id)
        if outfit is None:
            raise OutfitNotFoundError(f"outfit {outfit_id} not found")
        if outfit.active:
            raise OutfitStateError("穿着中的外观不能删除，请先切换到其他外观")

        _delete_reference_file(outfit)
        delete_portrait_file(outfit.fullbody_url)
        emit_ws_event(
            db,
            user_id=user_id,
            event_type="companion.outfit.updated",
            payload={"outfit_id": outfit.id, "worn": False},
        )
        await db.delete(outfit)
        await db.commit()


def schedule_outfit_description(user_id: int, outfit_id: int) -> None:
    key = (user_id, outfit_id)
    if key in _DESCRIBE_TASKS:
        return
    task = asyncio.create_task(
        _describe_outfit(user_id, outfit_id),
        name=f"companion.outfit.describe.{user_id}.{outfit_id}",
    )
    _DESCRIBE_TASKS[key] = task
    task.add_done_callback(lambda _task: _DESCRIBE_TASKS.pop(key, None))
    track_user_task(user_id, task)


async def drain_outfit_descriptions() -> None:
    tasks = list(_DESCRIBE_TASKS.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def _describe_outfit(user_id: int, outfit_id: int) -> None:
    """后台生成着装描述；读 → LLM（无会话）→ 写三段各自短会话，失败只记日志不阻塞就绪。"""
    try:
        async with SESSION_LOCAL() as db:
            persona = await get_or_create_persona(db, user_id)
            outfit = await _get_outfit(db, user_id, outfit_id)
            if outfit is None:
                return
            definition = safe_json_loads(persona.definition_json or "{}", default={})
            fullbody_url = outfit.fullbody_url
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
            }
        image_uri = await asyncio.to_thread(load_avatar_bytes_as_data_uri, fullbody_url)
        if not image_uri:
            return
        # 命名依据实际采纳的立绘，覆盖无文字的自备图与后续重绘。
        payload["outfit_visual_description"] = await describe_garment_image(user_id, image_uri)
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
        async with get_avatar_job_lock(user_id), SESSION_LOCAL() as db:
            outfit = await _get_outfit(db, user_id, outfit_id)
            if outfit is None or outfit.fullbody_url != fullbody_url:
                return
            if name:
                outfit.name = name
            if description:
                outfit.description = description
            emit_ws_event(
                db,
                user_id=user_id,
                event_type="companion.outfit.updated",
                payload={"outfit_id": outfit_id, "worn": False},
            )
            await db.commit()
    except Exception:
        logger.warning(
            "outfit description generation failed",
            extra={"user_id": user_id, "outfit_id": outfit_id},
            exc_info=True,
        )
