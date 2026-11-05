"""衣柜草稿、确认与删除；共享事务约束见本模块 README。"""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from infrastructure.ai.prompt_engineer import chat
from infrastructure.ai.selection import GenerationOrigin, ProviderSelection
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.assets.asset_store import build_data_uri, outfit_asset_directory, read_asset_data_uri
from infrastructure.assets.references import asset_paths
from infrastructure.database import SESSION_LOCAL
from infrastructure.domain_locks import lock_domain
from infrastructure.logger import get_logger
from infrastructure.text import parse_llm_json, resolve_language
from infrastructure.video_processing.ffmpeg import VideoProcessError
from infrastructure.video_processing.image import prepare_transparent_image
from infrastructure.video_processing.matting import require_matting_model
from runtime.background import TaskScope
from runtime.events.emit import emit_ws_event
from runtime.user_maintenance import is_user_in_maintenance
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from features.accounts.queries import user_is_active
from features.appearance.asset_bindings import commit_appearance
from features.appearance.avatar_service import (
    FULLBODY_ASPECT,
    FULLBODY_SIZE,
    AvatarGenerationError,
    delete_portrait_file,
    get_active_avatar,
    get_avatar_job_lock,
    persist_portrait_bytes,
    read_portrait_bytes,
    validate_transparent_portrait,
)
from features.appearance.identity import (
    VisualIdentityNotReadyError,
    require_visual_identity,
    visual_identity_is_current,
)
from features.appearance.models import AvatarAsset, CompanionOutfit, OutfitCreationJob, OutfitSource
from features.appearance.outfit_progress import read_outfit_progress, save_outfit_progress
from features.appearance.prompt_assembly import build_image_edit_prompt, build_outfit_prompt, describe_garment_image
from features.appearance.prompts import EDIT_PRESERVE_OUTFIT, OUTFIT_DESCRIBE_SYSTEM
from features.appearance.response_builders import outfit_response
from features.appearance.schemas import ImageReviseMode, OutfitResponse
from features.assets.lifecycle import cleanup_asset_directories, cleanup_user_assets, enqueue_asset_cleanup
from features.companion.persona_service import load_persona_definition
from features.companion.policy_contracts import OUTFIT_POLICY_DEFAULT
from features.companion.visual_state import (
    change_outfit_policy,
    ensure_persona_snapshot,
    lock_persona_snapshot,
    persona_snapshot,
)
from features.configuration.settings.values import get_user_setting
from features.media.character_images import (
    ImageChainState,
    ImageProgressWriter,
    generate_reviewed_images,
    image_asset_bytes,
)
from features.media.continuations import finish_media_operation, wait_for_media
from features.media.identity_contracts import VisualIdentitySnapshot
from features.media.image_generation import (
    ImageGenerationError,
    ImageReviewUnavailableError,
    generate_with_moderation_retry,
)
from features.media.review_contracts import VisualReview, review_passes
from features.media.review_evidence import review_media_intact

logger = get_logger(__name__)

# 草稿直接写入正式资产，避免可查看的生成结果因临时文件回收而丢失。
_DESCRIBE = TaskScope("appearance.outfit_descriptions")


def _valid_outfit_name(name: str, language: str) -> bool:
    """Keep the stored wardrobe label aligned with the naming contract in ``OUTFIT_DESCRIBE_SYSTEM``."""
    if language == "zh":
        return 1 <= len(name) <= 8
    return 1 <= len(name.split()) <= 5 and len(name) <= 64


class OutfitError(RuntimeError):
    """换装流程错误；str(exc) 恒为可展示的公开文案。"""


class OutfitNotFoundError(OutfitError):
    """目标外观行不存在或不属于调用者。"""


class OutfitStateError(OutfitError):
    """状态守卫拒绝（非法状态转换 / 删除保护）。"""


class OutfitContinuationBlocked(OutfitStateError):
    """原自主任务的采用权限已变化，候选与草稿继续保留。"""


class OutfitDraftUnavailableError(OutfitError):
    """草稿立绘缺失或无法读取，需要重新生成。"""


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
    uri = await asyncio.to_thread(read_asset_data_uri, avatar.seed_fullbody_url)
    if uri is None:
        raise OutfitStateError("全身形象缺失或无法读取，请在设置的“角色与记忆”中重新生成")
    return uri


async def get_outfit_policy(db: AsyncSession, user_id: int) -> str:
    persona = await persona_snapshot(db, user_id)
    return persona.outfit_policy if persona and persona.outfit_policy else OUTFIT_POLICY_DEFAULT


async def set_outfit_policy(db: AsyncSession, user_id: int, policy: str) -> str:
    if policy not in ("locked", "llm_may_replace"):
        raise OutfitStateError(f"unknown outfit policy: {policy}")
    await change_outfit_policy(db, user_id, policy)
    await commit_appearance(db, user_id)
    return policy


async def _get_outfit(db: AsyncSession, user_id: int, outfit_id: int, *, lock: bool = False) -> CompanionOutfit | None:
    statement = (
        select(CompanionOutfit)
        .where(CompanionOutfit.id == outfit_id, CompanionOutfit.user_id == user_id)
        .execution_options(populate_existing=True)
    )
    if lock:
        statement = statement.with_for_update()
    return (await db.execute(statement)).scalar_one_or_none()


async def _require_outfit_draft(db: AsyncSession, user_id: int, outfit_id: int) -> CompanionOutfit:
    outfit = await _get_outfit(db, user_id, outfit_id)
    if outfit is None:
        raise OutfitNotFoundError(f"outfit {outfit_id} not found")
    if outfit.status != "draft":
        raise OutfitStateError("仅草稿可以微调重绘")
    return outfit


async def list_outfits(db: AsyncSession, user_id: int) -> list[OutfitResponse]:
    """不取用户任务锁：生成可能长时间持锁，列表读取不必等它。"""
    outfits = (
        (
            await db.execute(
                select(CompanionOutfit)
                .where(CompanionOutfit.user_id == user_id)
                .order_by(CompanionOutfit.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    for outfit in outfits:
        if outfit.status == "ready" and outfit.description_status == "pending" and outfit.fullbody_url:
            schedule_outfit_description(user_id, outfit.id)
    return [outfit_response(outfit) for outfit in outfits]


async def _outfit_generation_context(
    db: AsyncSession, user_id: int
) -> tuple[AvatarAsset, str, VisualIdentitySnapshot, str]:
    avatar = await get_active_avatar(db, user_id)
    if avatar is None or not avatar.is_fullbody_confirmed:
        raise OutfitStateError("请先确认全身形象")
    persona = await ensure_persona_snapshot(db, user_id)
    if not persona.is_complete:
        raise OutfitStateError("请先完成引导再设计外观")
    definition = load_persona_definition(persona)
    species = str(definition.get("biological_type") or "").strip()
    try:
        identity = await require_visual_identity(db, user_id)
    except VisualIdentityNotReadyError as exc:
        raise OutfitStateError(str(exc)) from exc
    return (avatar, species, identity, str(definition.get("personality") or "").strip())


async def _describe_reference_garment(
    user_id: int,
    source: OutfitSource,
    image: bytes | None = None,
    content_type: str | None = None,
    requirement: str = "",
) -> str | None:
    """把服装参考图与用户文字整合为着装设计稿；视觉链缺失或失败返回 None（降级纯描述）。成功时写入 source 供调用方持久化，重生成不再重复整合；整合走独立短会话，不占请求连接。"""
    try:
        garment_uri = (
            await asyncio.to_thread(build_data_uri, image, content_type)
            if image is not None
            else await asyncio.to_thread(read_asset_data_uri, source.reference_image_path)
        )
        if garment_uri is None:
            return None
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
    identity: VisualIdentitySnapshot,
    outfit_id: int,
    image_edit: bool = False,
    selection: ProviderSelection | None = None,
    image_state: ImageChainState | None = None,
    design_description: str | None = None,
    expected_url: str | None = None,
    save_progress: ImageProgressWriter | None = None,
) -> tuple[str, GenerationOrigin | None, int | None, VisualReview]:
    manual_job_id: int | None = None
    if image_state is None:
        image_state = ImageChainState()
        async with SESSION_LOCAL() as session:
            manual_job = OutfitCreationJob(
                user_id=user_id,
                request_source="manual",
                description=prompt,
                state="running",
                checkpoint={
                    "outfit_id": outfit_id,
                    "manual_input": {
                        "prompt": prompt,
                        "reference_image": reference_image,
                        "identity_reference": identity_reference,
                        "identity": identity.model_dump(mode="json"),
                        "image_edit": image_edit,
                        "description": design_description,
                        "expected_url": expected_url,
                    },
                },
            )
            session.add(manual_job)
            await session.flush()
            manual_job_id = manual_job.id
            await wait_for_media(
                session,
                user_id=user_id,
                owner_kind="outfit.create",
                owner_job_id=manual_job.id,
                dependency_job_id=manual_job.id,
                operation_id=f"outfit:{manual_job.source_key}:continue",
                automatic=False,
                context={"source": "manual"},
            )
            await session.commit()

        async def save_manual(value: ImageChainState) -> None:
            await save_outfit_progress(
                user_id, manual_job_id, image_chain=value.model_dump(mode="json"), paths=value.stored_paths()
            )

        save_progress = save_manual

    async def generate(text: str) -> tuple[list[str], GenerationOrigin | None]:
        state = image_state if image_state is not None else ImageChainState()
        paths = await generate_reviewed_images(
            text,
            user_id=user_id,
            storage_directory=outfit_asset_directory(outfit_id),
            reference_image=reference_image,
            identity_reference=identity_reference,
            identity_reference_mode="primary",
            size=FULLBODY_SIZE,
            image_edit=image_edit,
            prefer_transparent_background=True,
            task=AiTaskKey.outfit,
            state=state,
            selection=selection,
            save_progress=save_progress,
        )
        return paths, state.selected_origin()

    try:
        paths, origin = await generate_with_moderation_retry(user_id, prompt, generate, task=AiTaskKey.outfit)
    except ImageGenerationError as exc:
        if manual_job_id is not None:
            async with SESSION_LOCAL() as session:
                manual_job = await session.get(OutfitCreationJob, manual_job_id, with_for_update=True)
                manual_job.state, manual_job.stage, manual_job.error = (
                    ("paused", "review_pending", str(exc))
                    if isinstance(exc, ImageReviewUnavailableError)
                    or bool((manual_job.checkpoint or {}).get("image_chain", {}).get("candidates"))
                    else ("failed", "failed", str(exc))
                )
                await session.commit()
        raise AvatarGenerationError(str(exc), internal=exc.internal) from exc
    except Exception as exc:
        # 供应商图片地址在图片链内下载，失败时没有候选；API 日志只记 internal，堆栈在此保留。
        logger.warning("outfit image generation failed", extra={"user_id": user_id}, exc_info=True)
        raise AvatarGenerationError("造型图片制作未完成，请稍后重试", internal=f"{type(exc).__name__}: {exc}") from exc
    retained = False
    try:
        data, _mime = await image_asset_bytes(paths[0])
        await asyncio.to_thread(validate_transparent_portrait, data)
        retained = True
        if manual_job_id is not None:
            async with SESSION_LOCAL() as session:
                manual_job = await session.get(OutfitCreationJob, manual_job_id, with_for_update=True)
                manual_job.state, manual_job.stage = "paused", "confirmation"
                await session.commit()
        selected = image_state.best()
        if selected is None or not review_passes(selected.review, ("identity", "goal", "quality")):
            raise OutfitStateError("原造型缺少适用的检查结论，候选已保留")
        return paths[0], origin, manual_job_id, selected.review
    except VideoProcessError as exc:
        raise AvatarGenerationError(str(exc), internal=exc.internal or str(exc)) from exc
    except ImageGenerationError as exc:
        raise AvatarGenerationError(str(exc), internal=exc.internal) from exc
    except OSError as exc:
        raise AvatarGenerationError("生成结果保存失败，请稍后重试", internal=f"{type(exc).__name__}: {exc}") from exc
    finally:
        if not retained and save_progress is None:
            for path in paths:
                await asyncio.to_thread(delete_portrait_file, path)


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


def _new_outfit_description(description: str | None, image: bytes | None) -> str:
    effective_description = (description or "").strip()
    if not effective_description and image is None:
        raise OutfitError("请先描述想要的着装，或上传一张参考图")
    return effective_description


async def _outfit_prompt(
    user_id: int,
    context: tuple[AvatarAsset, str, VisualIdentitySnapshot, str],
    identity_uri: str,
    *,
    requirement: str,
    feedback: str = "",
    previous_feedback: list[str] | None = None,
    external: bool = False,
) -> str:
    """新装与重绘共用装配入口；外部制作额外要求画幅与透明背景。"""
    _, species, _, personality = context
    return await build_outfit_prompt(
        user_id=user_id,
        reference_image=identity_uri,
        species=species,
        requirement=requirement,
        feedback=feedback,
        previous_feedback=previous_feedback,
        personality=personality,
        canvas_aspect=FULLBODY_ASPECT if external else None,
        require_transparent_background=external,
    )


async def _new_outfit_requirement(
    user_id: int, source: OutfitSource, image: bytes | None, content_type: str | None
) -> str:
    """新装要求：参考图整合出的设计稿优先，其次用户原话。"""
    garment_text = (
        await _describe_reference_garment(user_id, source, image, content_type, requirement=source.description)
        if image is not None
        else None
    )
    return garment_text or source.description or "为角色设计一套新的着装"


async def _commit_outfit_draft_image(
    db: AsyncSession, user_id: int, outfit: CompanionOutfit, *, image_url: str, source: OutfitSource, original_url: str
) -> None:
    """调用方完成草稿和身份复核后，同事务保存替换结果、通知与旧图回收登记。"""
    outfit.fullbody_url = image_url
    outfit.source_json = source.dump()
    emit_ws_event(db, user_id=user_id, event_type="companion.outfit.updated", payload={"outfit_id": outfit.id})
    await enqueue_asset_cleanup(db, user_id, [original_url])
    await commit_appearance(db, user_id)


@asynccontextmanager
async def _draft_swap(db: AsyncSession, user_id: int, *, new_url: str, original_url: str) -> AsyncIterator[None]:
    """换草稿文件生命周期：失败回收新图；成功后回收旧图并触发资产清理。"""
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
    job_id: int | None = None,
) -> CompanionOutfit:
    """根据文字与可选服装参考图创建外观草稿。"""
    effective_description = _new_outfit_description(description, image)

    progress = await read_outfit_progress(user_id, job_id) if job_id is not None else {}
    if progress.get("outfit_id"):
        existing = await _get_outfit(db, user_id, int(progress["outfit_id"]))
        if existing is not None:
            return existing

    context = await _outfit_generation_context(db, user_id)
    avatar, _, identity, _ = context
    identity_uri = await _require_fullbody_seed_readable(avatar)
    # 先领取序列值；生成等待不持有事务，也不暴露尚无图片的空草稿。
    outfit_id = progress.get("outfit_id") or await db.scalar(
        select(func.nextval(func.pg_get_serial_sequence("companion_outfits", "id")))
    )
    if outfit_id is None:
        raise OutfitStateError("外观编号分配失败")
    await commit_appearance(db, user_id)
    if job_id is not None:
        if progress.get("identity") and not await visual_identity_is_current(
            db, user_id, VisualIdentitySnapshot.model_validate(progress["identity"])
        ):
            raise OutfitContinuationBlocked("原任务身份已失效，候选与草稿已保留")
        if (
            progress.get("identity_revision", identity.revision) != identity.revision
            or progress.get("identity_path", avatar.seed_fullbody_url) != avatar.seed_fullbody_url
        ):
            raise OutfitStateError("角色身份已变化，不能继续原制作")
        await save_outfit_progress(
            user_id,
            job_id,
            outfit_id=outfit_id,
            identity_revision=identity.revision,
            identity_path=avatar.seed_fullbody_url,
            identity=identity.model_dump(mode="json"),
            paths={avatar.seed_fullbody_url},
        )
    await _require_outfit_matting(user_id)

    source = OutfitSource(
        description=effective_description,
        identity_revision=identity.revision,
        identity_reference_path=avatar.seed_fullbody_url,
    )
    image_state = ImageChainState.model_validate(progress.get("image_chain", {})) if job_id is not None else None
    prompt = (
        image_state.inputs.prompt
        if image_state is not None and image_state.inputs is not None
        else await _outfit_prompt(
            user_id,
            context,
            identity_uri,
            requirement=await _new_outfit_requirement(user_id, source, image, content_type),
        )
    )

    async def save_image(state: ImageChainState) -> None:
        if job_id is not None:
            await save_outfit_progress(
                user_id, job_id, image_chain=state.model_dump(mode="json"), paths=state.stored_paths()
            )

    draft_url, origin, manual_job_id, review = await _generate_outfit_fullbody(
        user_id,
        prompt=prompt,
        reference_image=avatar.seed_fullbody_url,
        identity_reference=avatar.seed_fullbody_url,
        identity=identity,
        outfit_id=outfit_id,
        image_state=image_state,
        design_description=effective_description,
        save_progress=save_image if job_id is not None else None,
    )

    source.generation_origin = origin
    source.review_result = review
    ref_path: str | None = None
    try:
        if image is not None:
            ref_path = await persist_portrait_bytes(
                user_id, image, content_type or "image/png", directory=outfit_asset_directory(outfit_id)
            )
            source.reference_image_path = ref_path
        async with get_avatar_job_lock(user_id):
            if not await visual_identity_is_current(db, user_id, identity):
                raise OutfitStateError("确认形象已更新，请重新生成外观")
            outfit = CompanionOutfit(
                id=outfit_id,
                user_id=user_id,
                name="新外观",
                fullbody_url=draft_url,
                status="draft",
                source_json=source.dump(),
            )
            db.add(outfit)
            if job_id is not None:
                await db.flush()
                creation = await db.get(OutfitCreationJob, job_id)
                if creation is None or creation.user_id != user_id:
                    raise OutfitStateError("原造型制作任务已失效")
                creation.result_outfit_id = outfit.id
            if manual_job_id is not None:
                manual_job = await db.get(OutfitCreationJob, manual_job_id, with_for_update=True)
                manual_job.result_outfit_id, manual_job.state, manual_job.stage = outfit.id, "succeeded", "complete"
                await finish_media_operation(
                    db, user_id, f"outfit:{manual_job.source_key}:continue", {"outfit_id": str(outfit.id)}
                )
            await commit_appearance(db, user_id)
    except BaseException:
        await db.rollback()
        delete_portrait_file(ref_path)
        if job_id is None:
            delete_portrait_file(draft_url)
        raise
    await db.refresh(outfit)
    return outfit


async def regenerate_outfit_draft(
    db: AsyncSession, user_id: int, outfit_id: int, *, feedback: str | None, mode: ImageReviseMode
) -> CompanionOutfit:
    """微调或重新生成外观；成功后回到草稿，等待用户确认。"""
    outfit = await _require_outfit_draft(db, user_id, outfit_id)
    original_url = outfit.fullbody_url
    original_status = outfit.status

    effective_feedback = (feedback or "").strip()
    if mode == "edit" and not effective_feedback:
        raise OutfitError("请先描述要微调的内容")

    context = await _outfit_generation_context(db, user_id)
    avatar, _, identity, _ = context

    source = OutfitSource.model_validate(outfit.source_json)
    await commit_appearance(db, user_id)
    await _require_outfit_matting(user_id)

    if mode == "edit":
        source_identity = source.identity_reference_path
        if source_identity != avatar.seed_fullbody_url:
            raise OutfitStateError("全身形象已变化，请按当前形象重新生成外观")
        # 编辑保持上一版图像的身份与造型；独立检查仍比较当前全身身份图。
        reference_uri = await asyncio.to_thread(read_asset_data_uri, original_url)
        if not reference_uri:
            raise OutfitDraftUnavailableError("上一版草稿图片无法读取，请改用重新生成")
        prompt = build_image_edit_prompt(effective_feedback, preserve=EDIT_PRESERVE_OUTFIT)
        await _require_fullbody_seed_readable(avatar)
    else:
        reference_uri = await _require_fullbody_seed_readable(avatar)
        requirement = await _outfit_requirement(user_id, source)
        _require_outfit_design(requirement, effective_feedback, source.feedback_history)
        prompt = await _outfit_prompt(
            user_id,
            context,
            reference_uri,
            requirement=requirement,
            feedback=effective_feedback,
            previous_feedback=source.feedback_history,
        )

    draft_url, origin, manual_job_id, review = await _generate_outfit_fullbody(
        user_id,
        prompt=prompt,
        reference_image=original_url if mode == "edit" else avatar.seed_fullbody_url,
        identity_reference=avatar.seed_fullbody_url,
        identity=identity,
        outfit_id=outfit_id,
        image_edit=mode == "edit",
        design_description=source.description,
        expected_url=original_url,
        selection=ProviderSelection(origin=source.generation_origin, mode="current" if mode == "edit" else "next"),
    )

    async with _draft_swap(db, user_id, new_url=draft_url, original_url=original_url), get_avatar_job_lock(user_id):
        outfit = await _get_outfit(db, user_id, outfit_id, lock=True)
        if outfit is None or outfit.status != original_status or outfit.fullbody_url != original_url:
            raise OutfitStateError("外观已发生变化，请刷新后重试")
        if not await visual_identity_is_current(db, user_id, identity):
            raise OutfitStateError("确认形象已更新，请重新生成外观")
        source.identity_revision = identity.revision
        source.identity_reference_path = avatar.seed_fullbody_url
        source.generation_origin = origin
        source.review_result = review
        if effective_feedback:
            source.feedback_history = [*source.feedback_history, effective_feedback]
        if manual_job_id is not None:
            manual_job = await db.get(OutfitCreationJob, manual_job_id, with_for_update=True)
            manual_job.result_outfit_id, manual_job.state, manual_job.stage = outfit.id, "succeeded", "complete"
            await finish_media_operation(
                db, user_id, f"outfit:{manual_job.source_key}:continue", {"outfit_id": str(outfit.id)}
            )
        await _commit_outfit_draft_image(
            db, user_id, outfit, image_url=draft_url, source=source, original_url=original_url
        )
    await db.refresh(outfit)
    return outfit


async def confirm_outfit(
    db: AsyncSession, user_id: int, outfit_id: int, *, creation_job_id: int | None = None
) -> CompanionOutfit:
    """验收已保存的透明草稿并入柜，保留生成时的来源。"""
    async with get_avatar_job_lock(user_id):
        outfit = await _get_outfit(db, user_id, outfit_id)
        if outfit is None:
            raise OutfitNotFoundError(f"outfit {outfit_id} not found")
        if outfit.status != "draft":
            raise OutfitStateError("仅草稿可以确认")
        original_url = outfit.fullbody_url
        original_source = outfit.source_json
        OutfitSource.model_validate(original_source)
        await commit_appearance(db, user_id)
        draft = await asyncio.to_thread(read_portrait_bytes, original_url)
        if draft is None:
            raise OutfitDraftUnavailableError("外观草稿图片无法读取，请重新生成")
        try:
            await asyncio.to_thread(validate_transparent_portrait, draft[0])
        except VideoProcessError as exc:
            logger.warning("outfit transparency validation failed", extra={"user_id": user_id}, exc_info=True)
            raise OutfitError(str(exc)) from exc
        persisted: str | None = None
        try:
            if original_url.startswith("temp-media/"):
                persisted = await persist_portrait_bytes(
                    user_id, draft[0], "image/png", directory=outfit_asset_directory(outfit_id)
                )
            outfit = await _get_outfit(db, user_id, outfit_id, lock=True)
            if (
                outfit is None
                or outfit.status != "draft"
                or outfit.fullbody_url != original_url
                or outfit.source_json != original_source
            ):
                raise OutfitStateError("外观已发生变化，请刷新后重试")
            outfit.fullbody_url = persisted or original_url
            if creation_job_id is not None:
                creation = await db.get(OutfitCreationJob, creation_job_id, with_for_update=True)
                if (
                    creation is None
                    or creation.user_id != user_id
                    or creation.state != "running"
                    or creation.cancel_requested
                    or not await user_is_active(db, user_id)
                ):
                    raise OutfitContinuationBlocked("原造型任务已取消或账户不可用，草稿已保留")
                chain = ImageChainState.model_validate((creation.checkpoint or {}).get("image_chain", {}))
                selected = chain.best()
                if (
                    selected is None
                    or selected.path != original_url
                    or not review_passes(selected.review, ("identity", "goal", "quality"))
                    or not await asyncio.to_thread(review_media_intact, selected.review.evidence, original_url)
                ):
                    raise OutfitContinuationBlocked("草稿或检查依据已变化，原候选已保留")
                try:
                    persona = await lock_persona_snapshot(db, user_id)
                except ValueError as exc:
                    raise OutfitContinuationBlocked("角色资料尚未就绪，候选与草稿已保留") from exc
                frozen = (creation.checkpoint or {}).get("identity")
                if (
                    not persona.is_complete
                    or persona.outfit_policy == "locked"
                    or frozen is None
                    or not await visual_identity_is_current(db, user_id, VisualIdentitySnapshot.model_validate(frozen))
                ):
                    raise OutfitContinuationBlocked("原授权或身份已变化，候选与草稿已保留")
                source = OutfitSource.model_validate(outfit.source_json)
                source.review_result = selected.review
                outfit.source_json = source.dump()
                creation.result_outfit_id, creation.state, creation.stage, creation.error = (
                    outfit.id,
                    "succeeded",
                    "complete",
                    None,
                )
                await finish_media_operation(
                    db, user_id, f"outfit:{creation.source_key}:continue", {"outfit_id": str(outfit.id)}
                )
            outfit.status = "ready"
            emit_ws_event(
                db, user_id=user_id, event_type="companion.life.presentation_requested", payload={"force": True}
            )
            emit_ws_event(db, user_id=user_id, event_type="companion.outfit.updated", payload={"outfit_id": outfit.id})
            await commit_appearance(db, user_id)
        except BaseException:
            await db.rollback()
            delete_portrait_file(persisted)
            raise
    schedule_outfit_description(user_id, outfit.id)
    await db.refresh(outfit)
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
    effective_description = _new_outfit_description(description, image)

    context = await _outfit_generation_context(db, user_id)
    avatar, _, _, _ = context
    identity_uri = await _require_fullbody_seed_readable(avatar)
    await commit_appearance(db, user_id)

    return await _outfit_prompt(
        user_id,
        context,
        identity_uri,
        requirement=await _new_outfit_requirement(
            user_id,
            OutfitSource(description=effective_description, identity_reference_path=avatar.seed_fullbody_url),
            image,
            content_type,
        ),
        external=True,
    )


async def prepare_outfit_regenerate_prompt(
    db: AsyncSession, user_id: int, outfit_id: int, *, feedback: str | None
) -> str:
    """根据已有草稿设计与反馈生成外部制作提示词。"""
    outfit = await _require_outfit_draft(db, user_id, outfit_id)
    effective_feedback = (feedback or "").strip()

    context = await _outfit_generation_context(db, user_id)
    avatar, _, _, _ = context
    identity_uri = await _require_fullbody_seed_readable(avatar)
    source = OutfitSource.model_validate(outfit.source_json)
    await commit_appearance(db, user_id)

    requirement = await _outfit_requirement(user_id, source)
    _require_outfit_design(requirement, effective_feedback, source.feedback_history)
    return await _outfit_prompt(
        user_id,
        context,
        identity_uri,
        requirement=requirement,
        feedback=effective_feedback,
        previous_feedback=source.feedback_history,
        external=True,
    )


async def _prepare_adopted_outfit_image(
    db: AsyncSession, user_id: int, data: bytes
) -> tuple[VisualIdentitySnapshot, str, str]:
    """自备造型图先透明化并保存为正式资产；返回身份、身份原图与新图路径。"""
    avatar, _, identity, _ = await _outfit_generation_context(db, user_id)
    identity_path = avatar.seed_fullbody_url
    await commit_appearance(db, user_id)
    prepared = await _prepare_outfit_upload(user_id, data)
    return identity, identity_path, await persist_portrait_bytes(user_id, prepared, "image/png")


async def _require_current_outfit_identity(
    db: AsyncSession, user_id: int, *, identity: VisualIdentitySnapshot, identity_path: str
) -> None:
    current_avatar = await get_active_avatar(db, user_id)
    if (
        current_avatar is None
        or current_avatar.seed_fullbody_url != identity_path
        or not await visual_identity_is_current(db, user_id, identity)
    ):
        raise OutfitStateError("确认形象已更新，请重新上传外观")


async def adopt_outfit_draft_image(
    db: AsyncSession, user_id: int, *, description: str | None, data: bytes
) -> CompanionOutfit:
    """透明化自备全身图并创建草稿，确认后转正。"""
    identity, identity_path, fullbody_url = await _prepare_adopted_outfit_image(db, user_id, data)
    try:
        async with get_avatar_job_lock(user_id):
            await _require_current_outfit_identity(db, user_id, identity=identity, identity_path=identity_path)
            outfit = CompanionOutfit(
                user_id=user_id,
                name="新外观",
                fullbody_url=fullbody_url,
                status="draft",
                source_json=OutfitSource(
                    description=(description or "").strip(),
                    identity_revision=identity.revision,
                    identity_reference_path=identity_path,
                ).dump(),
            )
            db.add(outfit)
            await commit_appearance(db, user_id)
    except BaseException:
        await db.rollback()
        delete_portrait_file(fullbody_url)
        raise
    await db.refresh(outfit)
    return outfit


async def adopt_outfit_regenerate_image(
    db: AsyncSession, user_id: int, outfit_id: int, *, data: bytes
) -> CompanionOutfit:
    """透明化自备全身图后替换草稿，成功同事务发送更新事件。"""
    outfit = await _require_outfit_draft(db, user_id, outfit_id)
    original_url = outfit.fullbody_url
    identity, identity_path, fullbody_url = await _prepare_adopted_outfit_image(db, user_id, data)
    async with _draft_swap(db, user_id, new_url=fullbody_url, original_url=original_url), get_avatar_job_lock(user_id):
        outfit = await _get_outfit(db, user_id, outfit_id, lock=True)
        if outfit is None or outfit.status != "draft" or outfit.fullbody_url != original_url:
            raise OutfitStateError("外观已发生变化，请刷新后重试")
        await _require_current_outfit_identity(db, user_id, identity=identity, identity_path=identity_path)
        source = OutfitSource.model_validate(outfit.source_json)
        source.generation_origin = None
        source.review_result = None
        source.identity_revision = identity.revision
        source.identity_reference_path = identity_path
        await _commit_outfit_draft_image(
            db, user_id, outfit, image_url=fullbody_url, source=source, original_url=original_url
        )
    await db.refresh(outfit)
    return outfit


async def delete_outfit(db: AsyncSession, user_id: int, outfit_id: int) -> None:
    """删除外观素材；提交后立即回收无引用的专属文件。"""
    async with get_avatar_job_lock(user_id):
        await lock_domain(db, user_id, "appearance")
        outfit = await _get_outfit(db, user_id, outfit_id)
        if outfit is None:
            raise OutfitNotFoundError(f"outfit {outfit_id} not found")
        directory = outfit_asset_directory(outfit.id)
        paths = asset_paths([outfit.fullbody_url, outfit.source_json], user_id)
        await enqueue_asset_cleanup(db, user_id, paths, expedite=True)

        emit_ws_event(db, user_id=user_id, event_type="companion.outfit.updated", payload={"outfit_id": outfit.id})
        await db.delete(outfit)
        await commit_appearance(db, user_id)
    # 删除提交后再停止任务和清理文件；提交失败时外观仍完整可用。
    await _DESCRIBE.cancel_and_wait((user_id, outfit_id))
    await cleanup_asset_directories(user_id, [directory])
    if outfit.fullbody_url.startswith("temp-media/"):
        delete_portrait_file(outfit.fullbody_url)


def schedule_outfit_description(user_id: int, outfit_id: int) -> None:
    _DESCRIBE.spawn(
        (user_id, outfit_id),
        lambda: _describe_outfit(user_id, outfit_id),
        name=f"companion.outfit.describe.{user_id}.{outfit_id}",
        user_id=user_id,
    )


async def retry_outfit_description(db: AsyncSession, user_id: int, outfit_id: int) -> CompanionOutfit:
    async with get_avatar_job_lock(user_id):
        outfit = await _get_outfit(db, user_id, outfit_id)
        if outfit is None:
            raise OutfitNotFoundError("找不到外观")
        if outfit.status != "ready":
            raise OutfitStateError("请先确认外观参考图")
        if _DESCRIBE.running((user_id, outfit_id)) is not None:
            return outfit
        outfit.description_status, outfit.description_error = "pending", None
        await commit_appearance(db, user_id)
        schedule_outfit_description(user_id, outfit_id)
        return outfit


async def drain_outfit_descriptions() -> None:
    await _DESCRIBE.drain()


async def recover_outfit_descriptions() -> None:
    """进程重启不重复付费补全；中断的描述改为用户可手动重试。"""
    async with SESSION_LOCAL() as db:
        await db.execute(
            update(CompanionOutfit)
            .where(CompanionOutfit.status == "ready", CompanionOutfit.description_status.in_(("pending", "processing")))
            .values(description_status="failed", description_error="外观描述已中断，请重试补全")
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
            await commit_appearance(db, user_id)
        image_uri = await asyncio.to_thread(read_asset_data_uri, fullbody_url)
        if not image_uri:
            # 立绘被替换或已被清理时没有可命名的图片，外观保留原名。
            logger.info(
                "outfit description skipped: image unreadable", extra={"user_id": user_id, "outfit_id": outfit_id}
            )
            raise OutfitStateError("外观图片无法读取，请先修复参考图")
        # 命名依据实际采纳的立绘，覆盖无文字的自备图与后续重绘。
        if is_user_in_maintenance(user_id):
            raise asyncio.CancelledError
        payload["outfit_visual_description"] = await describe_garment_image(user_id, image_uri)
        if is_user_in_maintenance(user_id):
            raise asyncio.CancelledError
        raw = await chat(
            user_id, OUTFIT_DESCRIBE_SYSTEM, json.dumps(payload, ensure_ascii=False), task=AiTaskKey.outfit
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
        output_language = payload["output_language"]
        if not _valid_outfit_name(name, output_language) or not description or len(description) > 500:
            logger.warning(
                "outfit description output invalid",
                extra={
                    "user_id": user_id,
                    "outfit_id": outfit_id,
                    "output_chars": len(raw),
                    "name_chars": len(name),
                    "description_chars": len(description),
                },
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
            emit_ws_event(db, user_id=user_id, event_type="companion.outfit.updated", payload={"outfit_id": outfit_id})
            await commit_appearance(db, user_id)
    except asyncio.CancelledError:
        await _set_description_failure(user_id, outfit_id, fullbody_url, "外观描述已中断，请重试补全")
        raise
    except Exception:
        logger.warning(
            "outfit description generation failed", extra={"user_id": user_id, "outfit_id": outfit_id}, exc_info=True
        )
        await _set_description_failure(user_id, outfit_id, fullbody_url, "外观描述未完成，请重试补全")


async def _set_description_failure(user_id: int, outfit_id: int, path: str, message: str) -> None:
    async with SESSION_LOCAL() as db:
        outfit = await _get_outfit(db, user_id, outfit_id)
        if outfit is None or outfit.fullbody_url != path:
            return
        outfit.description_status, outfit.description_error = "failed", message
        emit_ws_event(db, user_id=user_id, event_type="companion.outfit.updated", payload={"outfit_id": outfit_id})
        await commit_appearance(db, user_id)
