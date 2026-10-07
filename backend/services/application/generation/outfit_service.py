"""衣柜草稿、确认、穿着与删除；共享事务约束见本模块 README。"""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

from components import (
    SESSION_LOCAL,
    SETTINGS,
    get_logger,
    is_user_in_maintenance,
    parse_llm_json,
    resolve_language,
    track_user_task,
    utc_now,
)
from modules.companion import (
    OUTFIT_POLICY_DEFAULT,
    AvatarAsset,
    CharacterCardSnapshot,
    CompanionOutfit,
    ImageReviseMode,
    OutfitResponse,
    OutfitSource,
    Persona,
)
from modules.settings import get_user_setting
from modules.ws import emit_ws_event
from prompts.generation import EDIT_PRESERVE_OUTFIT, OUTFIT_DESCRIBE_SYSTEM
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from services.domains.actions import delete_outfit_action_packs
from services.domains.assets import cleanup_user_assets, enqueue_asset_cleanup
from services.domains.companion import (
    CharacterCardNotReadyError,
    character_snapshot_is_current,
    get_character_card,
    get_or_create_persona,
    load_persona_definition,
    render_character_identity,
    require_character_snapshot,
)
from services.infrastructure.assets import build_data_uri, outfit_asset_directory, unlink_companion_asset
from services.infrastructure.llm import (
    chat,
)
from services.infrastructure.video_processing import VideoProcessError, prepare_transparent_image, require_matting_model

from .appearance_prompts import (
    build_image_edit_prompt,
    build_outfit_prompt,
    describe_garment_image,
)
from .avatar_service import (
    FULLBODY_ASPECT,
    FULLBODY_SIZE,
    AvatarGenerationError,
    delete_portrait_file,
    generate_with_moderation_retry,
    get_active_avatar,
    get_avatar_job_lock,
    load_avatar_bytes_as_data_uri,
    persist_portrait_bytes,
    persist_portrait_or_draft,
    read_portrait_bytes,
    validate_transparent_portrait,
)
from .character_images import generate_character_images, image_asset_bytes
from .image_generation import ImageGenerationError
from .media_chain import spawn_removed_action_task_cancellation
from .response_builders import outfit_response

logger = get_logger(__name__)

# 草稿立绘存于 temp-media：按其存活时长预留余量，读取时先于文件过期把草稿置为过期。
_DRAFT_TTL_MARGIN = timedelta(hours=1)
_DESCRIBE_TASKS: dict[tuple[int, int], asyncio.Task[None]] = {}


class OutfitError(RuntimeError):
    """换装流程错误；str(exc) 恒为可展示的公开文案。"""


class OutfitNotFoundError(OutfitError):
    """目标外观行不存在或不属于调用者。"""


class OutfitStateError(OutfitError):
    """状态守卫拒绝（非法状态转换 / 删除保护）。"""


class OutfitDraftExpiredError(OutfitError):
    """草稿立绘的 temp-media 文件已过期，需重新生成。"""


async def _require_outfit_matting(user_id: int) -> None:
    try:
        await asyncio.to_thread(require_matting_model)
    except VideoProcessError as exc:
        logger.warning("outfit matting unavailable", extra={"user_id": user_id}, exc_info=True)
        raise OutfitError(str(exc)) from exc


async def _prepare_outfit_upload(user_id: int, data: bytes) -> bytes:
    try:
        return await asyncio.to_thread(prepare_transparent_image, data)
    except VideoProcessError as exc:
        logger.warning("outfit image transparency failed", extra={"user_id": user_id}, exc_info=True)
        raise OutfitError(str(exc)) from exc


async def _require_fullbody_seed_readable(avatar: AvatarAsset) -> str:
    """换装/自备图身份锚点：全身种子路径存在且字节可读时返回 data URI。不可读是源资产状态冲突（与 avatar/scenes 自备图同语义），抛 OutfitStateError 由 API 映射 409，不能降级纯文字。"""
    if not avatar.seed_fullbody_url:
        raise OutfitStateError("全身形象缺失或无法读取，请在设置的“角色与记忆”中重新生成")
    uri = await asyncio.to_thread(load_avatar_bytes_as_data_uri, avatar.seed_fullbody_url)
    if uri is None:
        raise OutfitStateError("全身形象缺失或无法读取，请在设置的“角色与记忆”中重新生成")
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
    *,
    lock: bool = False,
) -> CompanionOutfit | None:
    statement = (
        select(CompanionOutfit)
        .where(CompanionOutfit.id == outfit_id, CompanionOutfit.user_id == user_id)
        .execution_options(populate_existing=True)
    )
    if lock:
        statement = statement.with_for_update()
    return (await db.execute(statement)).scalar_one_or_none()


def _draft_ttl() -> timedelta:
    """临时文件存活时长可热更新，每次清扫时按当前设置计算。"""
    ttl = timedelta(hours=SETTINGS.temp_file_ttl_hours)
    return max(ttl - _DRAFT_TTL_MARGIN, ttl / 2)


async def _sweep_stale(db: AsyncSession, user_id: int) -> None:
    """读取时顺带清扫：过期草稿置 expired 并清理其参考图上传文件。条件更新只认领仍为草稿的行，并发清扫不会重复处理。"""
    expired = (
        (
            await db.execute(
                update(CompanionOutfit)
                .where(
                    CompanionOutfit.user_id == user_id,
                    CompanionOutfit.status == "draft",
                    CompanionOutfit.updated_at < utc_now() - _draft_ttl(),
                )
                .values(status="expired")
                .returning(CompanionOutfit),
            )
        )
        .scalars()
        .all()
    )
    if not expired:
        return
    for outfit in expired:
        source = OutfitSource.load(outfit.source_json)
        await enqueue_asset_cleanup(db, user_id, [source.reference_image_path or "", outfit.fullbody_url])
        source.reference_image_path = ""
        outfit.source_json = source.dump()
    await db.commit()
    await cleanup_user_assets(user_id)


async def list_outfits(db: AsyncSession, user_id: int) -> list[OutfitResponse]:
    """不取用户任务锁：生成可能长时间持锁，列表与过期清扫都不必等它。"""
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
) -> tuple[AvatarAsset, str, CharacterCardSnapshot, str]:
    avatar = await get_active_avatar(db, user_id)
    if avatar is None or not avatar.is_fullbody_confirmed:
        raise OutfitStateError("请先确认全身形象")
    persona = await get_or_create_persona(db, user_id)
    if not persona.is_complete:
        raise OutfitStateError("请先完成引导再设计外观")
    definition = load_persona_definition(persona)
    species = str(definition.get("biological_type") or "").strip()
    try:
        identity = await require_character_snapshot(db, user_id)
    except CharacterCardNotReadyError as exc:
        raise OutfitStateError(str(exc)) from exc
    return (
        avatar,
        species,
        identity,
        str(definition.get("personality") or "").strip(),
    )


async def _describe_reference_garment(
    user_id: int,
    source: OutfitSource,
    image: bytes | None = None,
    content_type: str | None = None,
    requirement: str = "",
) -> str | None:
    """把服装参考图与用户文字整合为着装设计稿；视觉链缺失或失败返回 None（降级纯描述）。成功时写入 source 供调用方持久化，重生成不再重复整合；整合走独立短会话，不占请求连接。"""
    if image is None:
        loaded = await asyncio.to_thread(read_portrait_bytes, source.reference_image_path)
        if loaded is None:
            return None
        image, content_type = loaded
    try:
        garment_uri = await asyncio.to_thread(build_data_uri, image, content_type)
        text = await describe_garment_image(user_id, garment_uri, requirement)
    except Exception:
        logger.warning(
            "garment reference describe failed; falling back to text-only generation",
            extra={"user_id": user_id},
            exc_info=True,
        )
        return None
    source.reference_description = text
    return text


async def _generate_outfit_fullbody(
    user_id: int,
    *,
    prompt: str,
    reference_image: str,
    identity_reference: str,
    identity: CharacterCardSnapshot,
    outfit_id: int,
    image_edit: bool = False,
) -> str:
    async def generate(text: str) -> list[str]:
        return await generate_character_images(
            text,
            user_id=user_id,
            storage_directory=outfit_asset_directory(outfit_id),
            reference_image=reference_image,
            identity_reference=identity_reference,
            identity_text=render_character_identity(identity),
            size=FULLBODY_SIZE,
            image_edit=image_edit,
            prefer_transparent_background=True,
        )

    try:
        paths = await generate_with_moderation_retry(user_id, prompt, generate)
    except ImageGenerationError as exc:
        raise AvatarGenerationError(str(exc), internal=exc.internal) from exc
    except Exception as exc:
        # 供应商图片地址在图片链内下载，失败时没有候选；API 日志只记 internal，堆栈在此保留。
        logger.warning("outfit image generation failed", extra={"user_id": user_id}, exc_info=True)
        raise AvatarGenerationError("生成结果下载失败，请稍后重试", internal=f"{type(exc).__name__}: {exc}") from exc
    try:
        data, _mime = await image_asset_bytes(paths[0])
        await asyncio.to_thread(validate_transparent_portrait, data)
        return await persist_portrait_or_draft(data, user_id, "image/png", persist=False)
    except VideoProcessError as exc:
        raise AvatarGenerationError(str(exc), internal=exc.internal or str(exc)) from exc
    except ImageGenerationError as exc:
        raise AvatarGenerationError(str(exc), internal=exc.internal) from exc
    except OSError as exc:
        raise AvatarGenerationError("生成结果保存失败，请稍后重试", internal=f"{type(exc).__name__}: {exc}") from exc
    finally:
        for path in paths:
            await asyncio.to_thread(unlink_companion_asset, path)


async def _outfit_requirement(user_id: int, source: OutfitSource) -> str:
    """草稿重绘沿用的着装要求：优先参考图设计稿（创建时整合失败则补一次），缺失时退回用户原话。"""
    garment_text = source.reference_description
    if not garment_text and source.reference_image_path:
        garment_text = await _describe_reference_garment(user_id, source, requirement=source.description)
    return garment_text or source.description


def _require_outfit_design(requirement: str, feedback: str, previous_feedback: list[str]) -> None:
    """重绘至少需要原设计、已接受反馈或本次反馈之一；否则只会生成身份图的默认造型。"""
    if not (requirement or feedback or previous_feedback):
        raise OutfitError("请先描述想要的着装或修改要求")


@asynccontextmanager
async def _draft_swap(db: AsyncSession, user_id: int, *, new_url: str, original_url: str) -> AsyncIterator[None]:
    """换草稿文件生命周期：失败回收新图；成功后回收旧 temp 草稿并触发资产清理。"""
    try:
        yield
    except BaseException:
        await db.rollback()
        delete_portrait_file(new_url)
        raise
    if original_url != new_url and original_url.startswith("temp-media/"):
        delete_portrait_file(original_url)
    await cleanup_user_assets(user_id)


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

    avatar, species, identity, personality = await _outfit_generation_context(db, user_id)
    identity_uri = await _require_fullbody_seed_readable(avatar)
    # 先领取序列值；生成等待不持有事务，也不暴露尚无图片的空草稿。
    outfit_id = await db.scalar(select(func.nextval(func.pg_get_serial_sequence("companion_outfits", "id"))))
    if outfit_id is None:
        raise OutfitStateError("外观编号分配失败")
    await db.commit()
    await _require_outfit_matting(user_id)

    source = OutfitSource(
        description=effective_description,
        character_card_revision=identity.revision,
        identity_reference_path=avatar.seed_fullbody_url,
    )
    garment_text: str | None = None
    if image is not None:
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
        identity=render_character_identity(identity),
        personality=personality,
        requirement=feedback,
    )
    draft_url = await _generate_outfit_fullbody(
        user_id,
        prompt=prompt,
        reference_image=identity_uri,
        identity_reference=identity_uri,
        identity=identity,
        outfit_id=outfit_id,
    )

    ref_path: str | None = None
    try:
        if image is not None:
            ref_path = await persist_portrait_bytes(
                user_id,
                image,
                content_type or "image/png",
                directory=outfit_asset_directory(outfit_id),
            )
            source.reference_image_path = ref_path
        async with get_avatar_job_lock(user_id):
            if not await character_snapshot_is_current(db, user_id, identity):
                raise OutfitStateError("角色卡已更新，请重新生成外观")
            outfit = CompanionOutfit(
                id=outfit_id,
                user_id=user_id,
                name="新外观",
                fullbody_url=draft_url,
                status="draft",
                source_json=source.dump(),
            )
            db.add(outfit)
            await db.commit()
    except BaseException:
        await db.rollback()
        for path in (ref_path, draft_url):
            delete_portrait_file(path)
        raise
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
    if outfit.status != "draft":
        raise OutfitStateError("仅草稿可以微调重绘")
    original_url = outfit.fullbody_url
    original_status = outfit.status

    effective_feedback = (feedback or "").strip()
    if mode == "edit" and not effective_feedback:
        raise OutfitError("请先描述要微调的内容")

    avatar, species, identity, personality = await _outfit_generation_context(db, user_id)

    source = OutfitSource.load(outfit.source_json)
    await db.commit()
    await _require_outfit_matting(user_id)

    if mode == "edit":
        source_identity = source.identity_reference_path
        if not source_identity or source_identity != avatar.seed_fullbody_url:
            raise OutfitStateError("全身形象已变化，请按当前形象重新生成外观")
        # 编辑保持上一版图像的身份与造型；独立评分仍比较当前全身身份图。
        reference_uri = await asyncio.to_thread(load_avatar_bytes_as_data_uri, original_url)
        if not reference_uri:
            raise OutfitDraftExpiredError("上一版草稿已过期或无法读取，请改用重新生成")
        prompt = build_image_edit_prompt(
            effective_feedback,
            preserve=EDIT_PRESERVE_OUTFIT + "\n" + render_character_identity(identity),
        )
        identity_uri = await _require_fullbody_seed_readable(avatar)
    else:
        reference_uri = identity_uri = await _require_fullbody_seed_readable(avatar)
        requirement = await _outfit_requirement(user_id, source)
        previous_feedback = source.feedback_history
        _require_outfit_design(requirement, effective_feedback, previous_feedback)
        prompt = await build_outfit_prompt(
            user_id=user_id,
            reference_image=reference_uri,
            species=species,
            requirement=requirement,
            feedback=effective_feedback,
            previous_feedback=previous_feedback,
            identity=render_character_identity(identity),
            personality=personality,
        )

    draft_url = await _generate_outfit_fullbody(
        user_id,
        prompt=prompt,
        reference_image=reference_uri,
        identity_reference=identity_uri,
        identity=identity,
        outfit_id=outfit_id,
        image_edit=mode == "edit",
    )

    async with _draft_swap(db, user_id, new_url=draft_url, original_url=original_url), get_avatar_job_lock(user_id):
        outfit = await _get_outfit(db, user_id, outfit_id, lock=True)
        if outfit is None or outfit.status != original_status or outfit.fullbody_url != original_url:
            raise OutfitStateError("外观已发生变化，请刷新后重试")
        if not await character_snapshot_is_current(db, user_id, identity):
            raise OutfitStateError("角色卡已更新，请重新生成外观")
        source.character_card_revision = identity.revision
        source.identity_reference_path = avatar.seed_fullbody_url
        outfit.fullbody_url = draft_url
        if effective_feedback:
            source.feedback_history = [*source.feedback_history, effective_feedback]
        outfit.source_json = source.dump()
        emit_ws_event(
            db,
            user_id=user_id,
            event_type="companion.outfit.updated",
            payload={"outfit_id": outfit.id, "worn": False},
        )
        await enqueue_asset_cleanup(db, user_id, [original_url])
        await db.commit()
    await db.refresh(outfit)
    return outfit


async def confirm_outfit(
    db: AsyncSession,
    user_id: int,
    outfit_id: int,
) -> CompanionOutfit:
    """验收透明草稿并转正为 ready；描述后台生成，穿着由动作包启用完成。"""
    async with get_avatar_job_lock(user_id):
        outfit = await _get_outfit(db, user_id, outfit_id)
        if outfit is None:
            raise OutfitNotFoundError(f"outfit {outfit_id} not found")
        if outfit.status != "draft":
            raise OutfitStateError("仅草稿可以确认")
        original_url = outfit.fullbody_url
        original_source = outfit.source_json
        source = OutfitSource.load(original_source)
        if source.character_card_revision is None or not source.identity_reference_path:
            raise OutfitStateError("外观缺少身份来源，请重新生成或上传")
        await db.commit()
        draft = await asyncio.to_thread(read_portrait_bytes, original_url)
        if draft is None:
            raise OutfitDraftExpiredError("外观草稿已过期，请重新生成")
        try:
            await asyncio.to_thread(validate_transparent_portrait, draft[0])
        except VideoProcessError as exc:
            logger.warning("outfit transparency validation failed", extra={"user_id": user_id}, exc_info=True)
            raise OutfitError(str(exc)) from exc
        persisted: str | None = None
        try:
            if original_url.startswith("temp-media/"):
                persisted = await persist_portrait_bytes(
                    user_id,
                    draft[0],
                    "image/png",
                    directory=outfit_asset_directory(outfit_id),
                )
            outfit = await _get_outfit(db, user_id, outfit_id, lock=True)
            if (
                outfit is None
                or outfit.status != "draft"
                or outfit.fullbody_url != original_url
                or outfit.source_json != original_source
            ):
                raise OutfitStateError("外观已发生变化，请刷新后重试")
            card = await get_character_card(db, user_id, lock=True)
            if card is None or card.status != "ready" or card.revision != source.character_card_revision:
                raise OutfitStateError("角色卡已更新，请重新生成外观后再确认")
            avatar = await get_active_avatar(db, user_id)
            if avatar is None or avatar.seed_fullbody_url != source.identity_reference_path:
                raise OutfitStateError("全身形象已变化，请重新生成外观后再确认")
            outfit.fullbody_url = persisted or original_url
            outfit.status = "ready"
            await db.commit()
        except BaseException:
            await db.rollback()
            delete_portrait_file(persisted)
            raise
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

    avatar, species, identity, personality = await _outfit_generation_context(db, user_id)
    identity_uri = await _require_fullbody_seed_readable(avatar)
    await db.commit()

    garment_text: str | None = None
    if image is not None:
        garment_text = await _describe_reference_garment(
            user_id,
            OutfitSource(),
            image,
            content_type,
            requirement=effective_description,
        )
    feedback = garment_text or effective_description or "为角色设计一套新的着装"
    return await build_outfit_prompt(
        user_id=user_id,
        reference_image=identity_uri,
        species=species,
        requirement=feedback,
        identity=render_character_identity(identity),
        personality=personality,
        canvas_aspect=FULLBODY_ASPECT,
        require_transparent_background=True,
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
    if outfit.status != "draft":
        raise OutfitStateError("仅草稿可以微调重绘")
    effective_feedback = (feedback or "").strip()

    avatar, species, identity, personality = await _outfit_generation_context(db, user_id)
    identity_uri = await _require_fullbody_seed_readable(avatar)
    source = OutfitSource.load(outfit.source_json)
    await db.commit()

    requirement = await _outfit_requirement(user_id, source)
    previous_feedback = source.feedback_history
    _require_outfit_design(requirement, effective_feedback, previous_feedback)
    return await build_outfit_prompt(
        user_id=user_id,
        reference_image=identity_uri,
        species=species,
        requirement=requirement,
        feedback=effective_feedback,
        previous_feedback=previous_feedback,
        identity=render_character_identity(identity),
        personality=personality,
        canvas_aspect=FULLBODY_ASPECT,
        require_transparent_background=True,
    )


async def adopt_outfit_draft_image(
    db: AsyncSession,
    user_id: int,
    *,
    description: str | None,
    data: bytes,
    content_type: str | None,
) -> CompanionOutfit:
    """透明化自备全身图并创建草稿，确认后转正。"""
    avatar, _, identity, _ = await _outfit_generation_context(db, user_id)
    identity_path = avatar.seed_fullbody_url
    await db.commit()
    data = await _prepare_outfit_upload(user_id, data)
    fullbody_url = await persist_portrait_or_draft(data, user_id, "image/png", persist=False)
    try:
        async with get_avatar_job_lock(user_id):
            current_avatar = await get_active_avatar(db, user_id)
            if (
                current_avatar is None
                or current_avatar.seed_fullbody_url != identity_path
                or not await character_snapshot_is_current(db, user_id, identity)
            ):
                raise OutfitStateError("角色形象或角色卡已更新，请重新上传外观")
            outfit = CompanionOutfit(
                user_id=user_id,
                name="新外观",
                fullbody_url=fullbody_url,
                status="draft",
                source_json=OutfitSource(
                    description=(description or "").strip(),
                    character_card_revision=identity.revision,
                    identity_reference_path=identity_path,
                ).dump(),
            )
            db.add(outfit)
            await db.commit()
    except BaseException:
        await db.rollback()
        delete_portrait_file(fullbody_url)
        raise
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
    """透明化自备全身图后替换草稿，成功同事务发送更新事件。"""
    outfit = await _get_outfit(db, user_id, outfit_id)
    if outfit is None:
        raise OutfitNotFoundError(f"outfit {outfit_id} not found")
    if outfit.status != "draft":
        raise OutfitStateError("仅草稿可以微调重绘")
    original_url = outfit.fullbody_url
    avatar, _, identity, _ = await _outfit_generation_context(db, user_id)
    identity_path = avatar.seed_fullbody_url
    await db.commit()
    data = await _prepare_outfit_upload(user_id, data)
    fullbody_url = await persist_portrait_or_draft(data, user_id, "image/png", persist=False)
    async with _draft_swap(db, user_id, new_url=fullbody_url, original_url=original_url), get_avatar_job_lock(user_id):
        outfit = await _get_outfit(db, user_id, outfit_id, lock=True)
        if outfit is None or outfit.status != "draft" or outfit.fullbody_url != original_url:
            raise OutfitStateError("外观已发生变化，请刷新后重试")
        current_avatar = await get_active_avatar(db, user_id)
        if (
            current_avatar is None
            or current_avatar.seed_fullbody_url != identity_path
            or not await character_snapshot_is_current(db, user_id, identity)
        ):
            raise OutfitStateError("角色形象或角色卡已更新，请重新上传外观")
        outfit.fullbody_url = fullbody_url
        source = OutfitSource.load(outfit.source_json)
        source.character_card_revision = identity.revision
        source.identity_reference_path = identity_path
        outfit.source_json = source.dump()
        emit_ws_event(
            db,
            user_id=user_id,
            event_type="companion.outfit.updated",
            payload={"outfit_id": outfit.id, "worn": False},
        )
        await enqueue_asset_cleanup(db, user_id, [original_url])
        await db.commit()
    await db.refresh(outfit)
    return outfit


async def activate_outfit(
    db: AsyncSession,
    user_id: int,
    outfit_id: int,
    *,
    require_current_identity: bool = False,
) -> CompanionOutfit:
    """即时穿着就绪外观：翻转该用户的激活外观。``active`` 是当前生效着装描述的唯一权威，供出镜媒体生成消费。"""
    async with get_avatar_job_lock(user_id):
        outfit = await _get_outfit(db, user_id, outfit_id)
        if outfit is None:
            raise OutfitNotFoundError(f"outfit {outfit_id} not found")
        if outfit.status != "ready":
            raise OutfitStateError("外观尚未确认，无法穿着")
        if require_current_identity:
            card = await get_character_card(db, user_id, lock=True)
            if card is None or OutfitSource.load(outfit.source_json).character_card_revision != card.revision:
                raise OutfitStateError("角色卡已更新，本次外观保留在衣柜，请重新生成后穿着")

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
    """删除非穿着外观与已停稳的关联动作包；正式资产在宽限期后按引用回收。"""
    async with get_avatar_job_lock(user_id):
        outfit = await _get_outfit(db, user_id, outfit_id)
        if outfit is None:
            raise OutfitNotFoundError(f"outfit {outfit_id} not found")
        if outfit.active:
            raise OutfitStateError("穿着中的外观不能删除，请先切换到其他外观")
        try:
            removed_tasks = await delete_outfit_action_packs(db, user_id, outfit.id)
        except ValueError as exc:
            raise OutfitStateError(str(exc)) from exc
        await enqueue_asset_cleanup(
            db,
            user_id,
            [outfit.fullbody_url, OutfitSource.load(outfit.source_json).reference_image_path or ""],
        )

        emit_ws_event(
            db,
            user_id=user_id,
            event_type="companion.outfit.updated",
            payload={"outfit_id": outfit.id, "worn": False},
        )
        await db.delete(outfit)
        await db.commit()
        # 删除提交后再清理文件与撤销远端任务；提交失败时外观仍完整可用。
        spawn_removed_action_task_cancellation(user_id, removed_tasks)
        await cleanup_user_assets(user_id)
        if outfit.fullbody_url.startswith("temp-media/"):
            delete_portrait_file(outfit.fullbody_url)


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


async def wait_outfit_description(user_id: int, outfit_id: int) -> None:
    """等待本外观已经受理的描述任务；不隐式重复付费失败任务。"""
    task = _DESCRIBE_TASKS.get((user_id, outfit_id))
    if task is not None:
        await asyncio.wait_for(asyncio.shield(task), timeout=SETTINGS.llm_request_timeout_seconds * 2 + 30)


async def retry_outfit_description(db: AsyncSession, user_id: int, outfit_id: int) -> CompanionOutfit:
    async with get_avatar_job_lock(user_id):
        outfit = await _get_outfit(db, user_id, outfit_id)
        if outfit is None:
            raise OutfitNotFoundError("找不到外观")
        if outfit.status != "ready":
            raise OutfitStateError("请先确认外观参考图")
        if (user_id, outfit_id) in _DESCRIBE_TASKS:
            return outfit
        outfit.description_status, outfit.description_error = "pending", None
        await db.commit()
        schedule_outfit_description(user_id, outfit_id)
        return outfit


async def drain_outfit_descriptions() -> None:
    tasks = list(_DESCRIBE_TASKS.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def recover_outfit_descriptions() -> None:
    """进程重启不重复付费补全；中断的描述改为用户可手动重试。"""
    async with SESSION_LOCAL() as db:
        await db.execute(
            update(CompanionOutfit)
            .where(
                CompanionOutfit.status == "ready",
                CompanionOutfit.description_status.in_(("pending", "processing")),
            )
            .values(
                description_status="failed",
                description_error="外观描述已中断，请重试补全",
            ),
        )
        await db.commit()


async def _describe_outfit(user_id: int, outfit_id: int) -> None:
    """后台生成着装描述；读 → LLM（无会话）→ 写三段各自短会话，失败只记日志不阻塞就绪。"""
    fullbody_url = ""
    try:
        async with SESSION_LOCAL() as db:
            outfit = await _get_outfit(db, user_id, outfit_id)
            if outfit is None:
                return
            fullbody_url = outfit.fullbody_url
            outfit.description_status, outfit.description_error = "processing", None
            payload = {"output_language": resolve_language(await get_user_setting(db, user_id, "language"))}
            await db.commit()
        image_uri = await asyncio.to_thread(load_avatar_bytes_as_data_uri, fullbody_url)
        if not image_uri:
            # 立绘被替换或已被清理时没有可命名的图片，外观保留原名。
            logger.info(
                "outfit description skipped: image unreadable",
                extra={"user_id": user_id, "outfit_id": outfit_id},
            )
            raise OutfitStateError("外观图片无法读取，请先修复参考图")
        # 命名依据实际采纳的立绘，覆盖无文字的自备图与后续重绘。
        if is_user_in_maintenance(user_id):
            raise asyncio.CancelledError
        payload["outfit_visual_description"] = await describe_garment_image(user_id, image_uri)
        if is_user_in_maintenance(user_id):
            raise asyncio.CancelledError
        raw = await chat(
            user_id,
            OUTFIT_DESCRIBE_SYSTEM,
            json.dumps(payload, ensure_ascii=False),
        )
        parsed = parse_llm_json(raw)
        if not isinstance(parsed, dict):
            logger.warning(
                "outfit description output invalid",
                extra={
                    "user_id": user_id,
                    "outfit_id": outfit_id,
                    "category": "non_json" if parsed is None else "non_object",
                    "output_chars": len(raw),
                },
            )
            raise OutfitStateError("外观描述未完成，请重试补全")
        name = parsed.get("name")
        description = parsed.get("description")
        if not isinstance(name, str) or not isinstance(description, str):
            logger.warning("outfit description fields invalid", extra={"user_id": user_id, "outfit_id": outfit_id})
            raise OutfitStateError("外观描述未完成，请重试补全")
        name, description = name.strip(), description.strip()
        if not name or len(name) > 64 or not description or len(description) > 2000:
            logger.warning(
                "outfit description output empty",
                extra={"user_id": user_id, "outfit_id": outfit_id, "output_chars": len(raw)},
            )
            raise OutfitStateError("外观描述未完成，请重试补全")
        async with get_avatar_job_lock(user_id), SESSION_LOCAL() as db:
            outfit = await _get_outfit(db, user_id, outfit_id)
            if outfit is None or outfit.fullbody_url != fullbody_url:
                return
            if name:
                outfit.name = name
            if description:
                outfit.description = description
            outfit.description_status, outfit.description_error = "ready", None
            emit_ws_event(
                db,
                user_id=user_id,
                event_type="companion.outfit.updated",
                payload={"outfit_id": outfit_id, "worn": False},
            )
            await db.commit()
    except asyncio.CancelledError:
        await _set_description_failure(user_id, outfit_id, fullbody_url, "外观描述已中断，请重试补全")
        raise
    except Exception:
        logger.warning(
            "outfit description generation failed",
            extra={"user_id": user_id, "outfit_id": outfit_id},
            exc_info=True,
        )
        await _set_description_failure(user_id, outfit_id, fullbody_url, "外观描述未完成，请重试补全")


async def _set_description_failure(user_id: int, outfit_id: int, path: str, message: str) -> None:
    async with SESSION_LOCAL() as db:
        outfit = await _get_outfit(db, user_id, outfit_id)
        if outfit is None or outfit.fullbody_url != path:
            return
        outfit.description_status, outfit.description_error = "failed", message
        emit_ws_event(
            db,
            user_id=user_id,
            event_type="companion.outfit.updated",
            payload={"outfit_id": outfit_id, "worn": False},
        )
        await db.commit()
