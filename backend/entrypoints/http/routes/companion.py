import asyncio
import base64
from typing import Annotated

from fastapi import Body, HTTPException, Path, Request, Response, status
from features.actions.generation.catalog import list_pack_responses
from features.actions.generation.commands import delete_pack as delete_video_pack
from features.actions.generation.errors import VideoPackError, VideoPackNotFoundError, VideoPackStateError
from features.actions.media_contracts import (
    VideoPackCreateRequest,
    VideoPackEnsureRequest,
    VideoPackGenerateRequest,
    VideoPackListResponse,
    VideoPackResponse,
    VideoPackRetryRequest,
    VideoPackSelectionRequest,
)
from features.actions.views import (
    activate_pack_view,
    create_pack_from_clips_view,
    create_pack_from_reference_view,
    ensure_system_action_view,
    retry_pack_view,
)
from features.appearance.avatar_service import (
    ALLOWED_AVATAR_UPLOAD_MIME_TYPES,
    AvatarGenerationError,
    AvatarNotFoundError,
    AvatarSourceUnreadableError,
    FullbodyGenerationError,
    ImageSealedError,
    get_avatar_job_lock,
    latest_fullbody_candidate,
    prepare_avatar_prompt,
    prepare_fullbody_prompt,
    retry_fullbody_candidate_analysis,
)
from features.appearance.character_card import (
    CharacterCardConflictError,
    CharacterCardNotReadyError,
    request_character_extraction,
    update_character_card,
)
from features.appearance.character_card_models import CharacterCardExtract, CharacterCardResponse, CharacterCardUpdate
from features.appearance.character_extraction import schedule_character_extraction
from features.appearance.outfit_service import (
    OutfitDraftExpiredError,
    OutfitError,
    OutfitNotFoundError,
    OutfitStateError,
    delete_outfit,
    get_outfit_policy,
    list_outfits,
    prepare_outfit_prompt,
    prepare_outfit_regenerate_prompt,
    set_outfit_policy,
)
from features.appearance.schemas import (
    AvatarAssetResponse,
    AvatarFromImageRequest,
    AvatarGenerateRequest,
    AvatarHistoryResponse,
    AvatarPromptRequest,
    FullbodyAdoptRequest,
    FullbodyCandidateResponse,
    FullbodyConfirmRequest,
    FullbodyPromptRequest,
    FullbodyReferenceGenerateRequest,
    ImagePromptResponse,
    OutfitAdoptRequest,
    OutfitConfirmRequest,
    OutfitCreateRequest,
    OutfitImageImportRequest,
    OutfitListResponse,
    OutfitPolicyRequest,
    OutfitPolicyResponse,
    OutfitPromptRequest,
    OutfitRegenerateRequest,
    OutfitResponse,
)
from features.appearance.use_cases import check_avatar_regeneration, regenerate_avatar_view
from features.appearance.views import (
    accept_fullbody_candidate_view,
    adopt_avatar_seed_view,
    adopt_fullbody_seed_view,
    adopt_outfit_draft_image_view,
    adopt_outfit_regenerate_image_view,
    character_card_view,
    confirm_fullbody_seed_view,
    confirm_outfit_view,
    create_outfit_draft_view,
    finalize_avatar_view,
    generate_avatar_view,
    generate_fullbody_reference_view,
    get_active_avatar_view,
    list_avatar_history_view,
    regenerate_avatar_from_image_view,
    regenerate_outfit_draft_view,
    retry_outfit_description_view,
    select_avatar_view,
)
from features.companion.http_contracts import (
    IdleExpressionRequest,
    IdleExpressionResult,
    InteractionStatsRequest,
    InteractionStatsResult,
    ShouldActRequest,
)
from features.companion.interaction_stats import record_interaction
from features.companion.persona_service import (
    PersonaConflictError,
    PersonaValidationError,
    confirm_portrait,
)
from features.companion.schemas import (
    CompanionOperationResponse,
    PersonaResponse,
    PersonaUpdate,
    PortraitConfirmRequest,
)
from features.companion.should_act import ShouldActResult
from features.companion.views import get_persona_view, update_persona_view
from features.companion.visual_state import ensure_persona_snapshot
from features.media.decision_contracts import ResourceDecision
from features.media.review_contracts import MediaReviewPublication, MediaReviewResponse
from features.media.review_service import MediaReviewSnapshot, get_media_review, list_pending_media_reviews
from infrastructure.ai.error_classifier import LLMRuntimeError
from infrastructure.ai.llm_client import MissingLlmConfigError
from infrastructure.ai.prompt_engineer import VisualReasoningError
from infrastructure.assets.asset_store import (
    ImagePixelLimitError,
    UnsupportedImageFormatError,
    client_asset_url,
    resolve_companion_asset_path,
    sniff_media_ext,
    validate_image_bytes,
    verify_signed_asset_request,
)
from infrastructure.assets.http_range import serve_ranged_file
from infrastructure.config import SETTINGS
from infrastructure.constants import POSTGRES_INT4_MAX
from infrastructure.database import SESSION_LOCAL, DbSession
from infrastructure.http_contracts import operation_policy
from infrastructure.logger import get_logger
from infrastructure.request_errors import RequestError
from infrastructure.text import safe_json_loads
from pydantic import ValidationError
from workflows.companion.operations import companion_idle_expression, companion_should_act
from workflows.media_review import MediaReviewStateError, accept_media_review, reject_media_review

from entrypoints.http.auth import CurrentUser, OptionalSession
from entrypoints.http.rate_limit import limiter
from entrypoints.http.routes._http_errors import missing_config_error
from entrypoints.http.routing import get_router

router = get_router()
logger = get_logger(__name__)


@router.post(
    "/interactions",
    operation_id="companion.record_interaction",
    response_model=InteractionStatsResult,
)
async def post_interaction(user: CurrentUser, body: InteractionStatsRequest) -> InteractionStatsResult:
    return InteractionStatsResult.model_validate(await record_interaction(user.id, body.kind, body.hour))


@router.post(
    "/idle-expressions",
    operation_id="companion.idle_expression",
    response_model=IdleExpressionResult,
    openapi_extra=operation_policy(wait_for_model=True),
)
async def post_idle_expression(user: CurrentUser, body: IdleExpressionRequest) -> IdleExpressionResult:
    return IdleExpressionResult.model_validate(
        await companion_idle_expression(user.id, body.model_dump(exclude_unset=True)),
    )


@router.post(
    "/movement-decisions",
    operation_id="companion.movement_decision",
    response_model=ShouldActResult,
    openapi_extra=operation_policy(wait_for_model=True),
)
async def post_movement_decision(user: CurrentUser, body: ShouldActRequest) -> ShouldActResult:
    return ShouldActResult.model_validate(
        await companion_should_act(user.id, body.model_dump(exclude_unset=True)),
    )


def _media_review_response(row: MediaReviewSnapshot) -> MediaReviewResponse:
    publication = MediaReviewPublication.model_validate(row.publication) if row.publication else None
    title = publication.title if publication else ""
    system_slot = publication.system_slot if publication else ""
    return MediaReviewResponse(
        id=row.id,
        status=row.status,
        reason=row.reason,
        media_type=row.media_type,
        media_url=client_asset_url(row.media_url),
        title=title,
        system_slot=system_slot,
    )


@router.get(
    "/media-reviews",
    response_model=list[MediaReviewResponse],
)
async def get_pending_visual_media_reviews(user: CurrentUser) -> list[MediaReviewResponse]:
    return [_media_review_response(row) for row in await list_pending_media_reviews(user.id)]


@router.get(
    "/media-reviews/{review_id}",
    response_model=MediaReviewResponse,
)
async def get_visual_media_review(
    review_id: Annotated[int, Path(gt=0, le=POSTGRES_INT4_MAX)],
    user: CurrentUser,
) -> MediaReviewResponse:
    row = await get_media_review(user.id, review_id)
    if row is None:
        raise RequestError(status_code=404, message="待确认动作不存在")
    return _media_review_response(row)


@router.post(
    "/media-reviews/{review_id}/decision",
    response_model=MediaReviewResponse,
)
async def decide_visual_media_review(
    review_id: Annotated[int, Path(gt=0, le=POSTGRES_INT4_MAX)],
    body: ResourceDecision,
    user: CurrentUser,
) -> MediaReviewResponse:
    try:
        operation = accept_media_review if body.decision == "accept" else reject_media_review
        row = await operation(user.id, review_id)
    except MediaReviewStateError as exc:
        raise RequestError(status_code=409, message=str(exc)) from exc
    if row is None:
        raise RequestError(status_code=404, message="待确认动作不存在")
    expected = "accepted" if body.decision == "accept" else "rejected"
    if row.status != expected:
        raise RequestError(status_code=409, message="该媒体已有相反的采用结论")
    return _media_review_response(row)


@router.get(
    "/character-card",
    response_model=CharacterCardResponse | None,
    openapi_extra=operation_policy(identities=("desktop", "remote")),
)
async def get_character_card_route(user: CurrentUser, db: DbSession) -> CharacterCardResponse | None:
    return await character_card_view(db, user.id)


@router.patch(
    "/character-card",
    response_model=CharacterCardResponse,
    openapi_extra=operation_policy(identities=("desktop", "remote")),
)
async def patch_character_card_route(
    body: CharacterCardUpdate,
    user: CurrentUser,
    db: DbSession,
) -> CharacterCardResponse:
    try:
        return await update_character_card(db, user.id, body)
    except (CharacterCardConflictError, CharacterCardNotReadyError) as exc:
        raise RequestError(status_code=409, message=str(exc)) from exc


@router.post(
    "/character-card/extract",
    response_model=CharacterCardResponse,
    openapi_extra=operation_policy(identities=("desktop", "remote")),
)
@limiter.limit(lambda: f"{SETTINGS.companion_avatar_generate_rate_limit_per_minute}/minute")
async def extract_character_card_route(
    request: Request,
    body: CharacterCardExtract,
    user: CurrentUser,
    db: DbSession,
) -> CharacterCardResponse:
    try:
        result = await request_character_extraction(
            db,
            user.id,
            expected_avatar_id=body.expected_avatar_id,
            expected_revision=body.expected_revision,
        )
    except (CharacterCardConflictError, CharacterCardNotReadyError) as exc:
        raise RequestError(status_code=409, message=str(exc)) from exc
    schedule_character_extraction(user.id)
    return result


@router.get(
    "/persona",
    response_model=PersonaResponse,
    openapi_extra=operation_policy(identities=("desktop", "remote")),
)
async def get_persona(user: CurrentUser, db: DbSession) -> PersonaResponse:
    return await get_persona_view(db, user.id)


@router.put(
    "/persona",
    response_model=PersonaResponse,
    openapi_extra=operation_policy(identities=("desktop", "remote")),
)
async def put_persona(body: PersonaUpdate, user: CurrentUser, db: DbSession) -> PersonaResponse:
    data = safe_json_loads(body.definition_json, default={})
    try:
        result = await update_persona_view(db, user.id, data, expected_fields=body.expected_fields)
    except PersonaConflictError as exc:
        raise RequestError(status_code=409, message=str(exc)) from exc
    except PersonaValidationError as exc:
        raise RequestError(status_code=422, message="Persona validation error", details={"reason": str(exc)})
    return result


@router.post(
    "/portrait/confirm",
    response_model=CompanionOperationResponse,
    openapi_extra=operation_policy(wait_for_model=True),
)
async def post_portrait_confirm(
    body: PortraitConfirmRequest,
    user: CurrentUser,
    db: DbSession,
) -> CompanionOperationResponse:
    try:
        async with get_avatar_job_lock(user.id):
            previewed = await get_active_avatar_view(db, user.id)
            if previewed is None:
                raise RequestError(status_code=404, message="请先生成或上传头像")
            if previewed.id != body.expected_avatar_id:
                raise RequestError(status_code=409, message="当前头像已更新，请重新加载预览后确认")
            asset = await finalize_avatar_view(db, user.id)
            if asset is None:
                raise RequestError(status_code=404, message="请先生成或上传头像")
            await confirm_portrait(db, user.id)
    except AvatarSourceUnreadableError as exc:
        raise RequestError(
            status_code=409,
            message="头像文件已过期或缺失，请重新生成头像",
            details={"reason": str(exc)},
        )
    return CompanionOperationResponse(ok=True)


@router.get(
    "/avatar",
    response_model=AvatarAssetResponse,
    openapi_extra=operation_policy(identities=("desktop", "remote")),
)
async def get_avatar(user: CurrentUser, db: DbSession) -> AvatarAssetResponse:
    asset = await get_active_avatar_view(db, user.id)
    if asset is None:
        raise HTTPException(status_code=404, detail="No avatar found")
    return asset


@router.post(
    "/avatar",
    response_model=AvatarAssetResponse,
    status_code=status.HTTP_201_CREATED,
    openapi_extra=operation_policy(upload="image", wait_for_model=True),
)
@limiter.limit(lambda: f"{SETTINGS.companion_avatar_generate_rate_limit_per_minute}/minute")
async def post_avatar(
    request: Request,  # required by @limiter.limit
    user: CurrentUser,
    body: Annotated[AvatarGenerateRequest | AvatarFromImageRequest, Body(discriminator="source")],
) -> AvatarAssetResponse:
    async with SESSION_LOCAL() as pre_db:
        persona = await ensure_persona_snapshot(pre_db, user.id)
        if not persona.is_complete:
            raise RequestError(
                status_code=409,
                message="请先完成引导再生成形象",
                details={"reason": "persona is incomplete"},
            )
    try:
        if isinstance(body, AvatarFromImageRequest):
            raw, content_type = await _decode_upload_image(body.image, body.content_type)
            presentation, presentation_type = await _decode_optional_image(
                body.presentation_image,
                body.presentation_content_type,
            )
            asset = await regenerate_avatar_from_image_view(
                user_id=user.id,
                data=raw,
                content_type=content_type,
                description=body.description,
                presentation_data=presentation,
                presentation_content_type=presentation_type,
            )
        elif body.mode is not None:
            await check_avatar_regeneration(user.id)
            asset = await regenerate_avatar_view(user.id, body.feedback, body.mode)
        else:
            asset = await generate_avatar_view(user_id=user.id, feedback=body.feedback)
    except ImageSealedError as exc:
        raise RequestError(status_code=409, message="形象已确认锁定，无法重新生成", details={"reason": str(exc)})
    except AvatarGenerationError as exc:
        logger.warning("post_avatar generation failed", extra={"user_id": user.id, "error": exc.internal})
        raise RequestError(status_code=502, message="伙伴形象生成失败，请稍后重试", details={"reason": str(exc)})
    except MissingLlmConfigError as exc:
        logger.warning("post_avatar missing config", extra={"user_id": user.id, "error": str(exc)})
        raise missing_config_error() from exc
    return asset


async def _decode_upload_image(image_b64: str, content_type: str | None) -> tuple[bytes, str]:
    """声明或实际格式不支持抛 415；base64 损坏或图片无法完整解码抛 400。返回按实际字节识别的 MIME，声明类型只做快速拒绝。"""
    normalized = (content_type or "image/png").split(";")[0].strip().lower()
    if normalized not in ALLOWED_AVATAR_UPLOAD_MIME_TYPES:
        raise RequestError(status_code=415, message="仅支持 PNG / JPEG / WebP / GIF 图片")
    try:
        raw = base64.b64decode(image_b64, validate=True)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid base64 image data")
    try:
        return await asyncio.to_thread(validate_image_bytes, raw)
    except UnsupportedImageFormatError:
        raise RequestError(status_code=415, message="仅支持 PNG / JPEG / WebP / GIF 图片")
    except ImagePixelLimitError:
        raise RequestError(status_code=400, message="图片尺寸过大，无法安全解码，请缩小后重新上传")
    except Exception:
        # Pillow 各格式插件对损坏数据抛出的异常类型不统一，一律按图片无法读取处理。
        raise RequestError(
            status_code=400,
            message="图片无法读取，请换一张有效的 PNG / JPEG / WebP / GIF 图片",
        )


async def _decode_optional_image(image_b64: str | None, content_type: str | None) -> tuple[bytes | None, str | None]:
    return await _decode_upload_image(image_b64, content_type) if image_b64 else (None, None)


@router.post(
    "/avatar/prompt",
    response_model=ImagePromptResponse,
    openapi_extra=operation_policy(wait_for_model=True),
)
@limiter.limit(lambda: f"{SETTINGS.companion_avatar_generate_rate_limit_per_minute}/minute")
async def post_avatar_prompt(request: Request, body: AvatarPromptRequest, user: CurrentUser) -> ImagePromptResponse:
    try:
        prompt = await prepare_avatar_prompt(user.id, feedback=body.feedback, has_reference=body.has_reference)
    except AvatarGenerationError as exc:
        raise _avatar_request_error(exc)
    except MissingLlmConfigError as exc:
        raise missing_config_error("描述生成服务", action="仍可直接上传准备好的图片") from exc
    except (LLMRuntimeError, RuntimeError, ValidationError):
        logger.warning("avatar prompt failed", extra={"user_id": user.id}, exc_info=True)
        raise RequestError(
            status_code=502,
            message="提示词暂时无法生成，请稍后重试；仍可直接上传准备好的图片",
        )
    return ImagePromptResponse(prompt=prompt)


@router.post(
    "/avatar/adopt",
    response_model=AvatarAssetResponse,
    status_code=status.HTTP_201_CREATED,
    openapi_extra=operation_policy(upload="image", wait_for_model=True),
)
@limiter.limit(lambda: f"{SETTINGS.companion_avatar_generate_rate_limit_per_minute}/minute")
async def post_avatar_adopt(request: Request, body: FullbodyAdoptRequest, user: CurrentUser) -> AvatarAssetResponse:
    raw, content_type = await _decode_upload_image(body.image, body.content_type)
    try:
        asset = await adopt_avatar_seed_view(user_id=user.id, data=raw, content_type=content_type)
    except AvatarGenerationError as exc:
        raise _avatar_request_error(exc)
    return asset


@router.get(
    "/avatar/history",
    response_model=AvatarHistoryResponse,
)
async def get_avatar_history(user: CurrentUser, db: DbSession) -> AvatarHistoryResponse:
    history = await list_avatar_history_view(db, user.id)
    return AvatarHistoryResponse(history=history)


@router.put(
    "/avatar/{avatar_id}/select",
    response_model=AvatarAssetResponse,
)
async def put_avatar_select(
    avatar_id: Annotated[int, Path(gt=0, le=POSTGRES_INT4_MAX)],
    user: CurrentUser,
    db: DbSession,
) -> AvatarAssetResponse:
    try:
        async with get_avatar_job_lock(user.id):
            asset = await select_avatar_view(db, user.id, avatar_id)
    except AvatarNotFoundError as exc:
        raise RequestError(status_code=404, message="找不到对应的形象", details={"reason": str(exc)})
    except ImageSealedError as exc:
        raise RequestError(status_code=409, message="形象已确认锁定，无法重新生成", details={"reason": str(exc)})
    return asset


@router.post(
    "/avatar/{avatar_id}/fullbody/reference",
    response_model=AvatarAssetResponse | FullbodyCandidateResponse,
    openapi_extra=operation_policy(upload="image", wait_for_model=True),
)
@limiter.limit(lambda: f"{SETTINGS.companion_avatar_generate_rate_limit_per_minute}/minute")
async def post_fullbody_reference(
    request: Request,
    avatar_id: Annotated[int, Path(gt=0, le=POSTGRES_INT4_MAX)],
    body: FullbodyReferenceGenerateRequest,
    user: CurrentUser,
) -> AvatarAssetResponse | FullbodyCandidateResponse:
    raw, content_type = await _decode_optional_image(body.image, body.content_type)
    try:
        asset = await generate_fullbody_reference_view(
            user.id,
            avatar_id=avatar_id,
            feedback=body.feedback,
            reference_image=raw,
            reference_content_type=content_type,
            mode=body.mode,
            candidate_id=body.candidate_id,
        )
    except (AvatarNotFoundError, AvatarSourceUnreadableError, VisualReasoningError) as exc:
        raise _avatar_request_error(exc) from exc
    except FullbodyGenerationError as exc:
        # 供应商链失败包装层：str 已透传公开文案（如编辑能力缺失指引），502 语义是可重试失败。
        logger.warning("fullbody reference generation failed", extra={"user_id": user.id, "error": exc.internal})
        raise RequestError(status_code=502, message=str(exc))
    except AvatarGenerationError as exc:
        # edit 守卫（反馈缺失、参考图同给等）是确定性的请求错误，公开文案直达用户。
        logger.warning("fullbody reference guard rejected", extra={"user_id": user.id, "error": exc.internal})
        raise RequestError(status_code=400, message=str(exc))
    except MissingLlmConfigError as exc:
        raise missing_config_error() from exc
    return asset


@router.get(
    "/avatar/{avatar_id}/fullbody/candidate",
    response_model=FullbodyCandidateResponse | None,
)
async def get_fullbody_candidate(
    avatar_id: Annotated[int, Path(gt=0, le=POSTGRES_INT4_MAX)],
    user: CurrentUser,
) -> FullbodyCandidateResponse | None:
    return await latest_fullbody_candidate(user.id, avatar_id)


@router.post(
    "/avatar/{avatar_id}/fullbody/candidate/{candidate_id}/analyze",
    response_model=FullbodyCandidateResponse,
    openapi_extra=operation_policy(wait_for_model=True),
)
async def post_fullbody_candidate_analyze(
    avatar_id: Annotated[int, Path(gt=0, le=POSTGRES_INT4_MAX)],
    candidate_id: Annotated[int, Path(gt=0, le=POSTGRES_INT4_MAX)],
    user: CurrentUser,
) -> FullbodyCandidateResponse:
    try:
        return await retry_fullbody_candidate_analysis(user.id, avatar_id, candidate_id)
    except AvatarNotFoundError as exc:
        raise RequestError(status_code=404, message=str(exc)) from exc
    except AvatarGenerationError as exc:
        raise RequestError(status_code=409, message=str(exc)) from exc


@router.post(
    "/avatar/{avatar_id}/fullbody/candidate/{candidate_id}/accept",
    response_model=AvatarAssetResponse,
    openapi_extra=operation_policy(wait_for_model=True),
)
async def post_fullbody_candidate_accept(
    avatar_id: Annotated[int, Path(gt=0, le=POSTGRES_INT4_MAX)],
    candidate_id: Annotated[int, Path(gt=0, le=POSTGRES_INT4_MAX)],
    user: CurrentUser,
) -> AvatarAssetResponse:
    try:
        asset = await accept_fullbody_candidate_view(user.id, avatar_id, candidate_id)
    except (AvatarGenerationError, VisualReasoningError) as exc:
        raise _avatar_request_error(exc) from exc
    return asset


@router.post(
    "/avatar/{avatar_id}/fullbody/confirm",
    response_model=AvatarAssetResponse,
    openapi_extra=operation_policy(wait_for_model=True),
)
@limiter.limit(lambda: f"{SETTINGS.companion_avatar_generate_rate_limit_per_minute}/minute")
async def post_fullbody_confirm(
    request: Request,
    avatar_id: Annotated[int, Path(gt=0, le=POSTGRES_INT4_MAX)],
    body: FullbodyConfirmRequest,
    user: CurrentUser,
) -> AvatarAssetResponse:
    try:
        asset = await confirm_fullbody_seed_view(user.id, avatar_id=avatar_id, expected_url=body.expected_url)
    except AvatarGenerationError as exc:
        raise _avatar_request_error(exc)
    schedule_character_extraction(user.id)
    return asset


def _avatar_request_error(exc: AvatarGenerationError | VisualReasoningError) -> RequestError:
    """自备图提示词/采纳端点共用的错误映射：语义与对应生成端点一致。"""
    if isinstance(exc, CharacterCardNotReadyError):
        return RequestError(status_code=409, message=str(exc))
    if isinstance(exc, VisualReasoningError):
        return RequestError(status_code=502, message=str(exc))
    if isinstance(exc, AvatarNotFoundError):
        return RequestError(status_code=404, message="找不到对应的形象", details={"reason": str(exc)})
    if isinstance(exc, ImageSealedError):
        return RequestError(status_code=409, message="形象已确认锁定，无法重新生成", details={"reason": str(exc)})
    if isinstance(exc, AvatarSourceUnreadableError):
        # 与 AI 生成端点一致：种子/头像文件不可读是需用户先修复源资产的状态冲突，不是普通请求错误。
        return RequestError(status_code=409, message=str(exc))
    return RequestError(status_code=400, message=str(exc))


@router.post(
    "/avatar/{avatar_id}/fullbody/reference/prompt",
    response_model=ImagePromptResponse,
    openapi_extra=operation_policy(wait_for_model=True),
)
@limiter.limit(lambda: f"{SETTINGS.companion_avatar_generate_rate_limit_per_minute}/minute")
async def post_fullbody_prompt(
    request: Request,
    avatar_id: Annotated[int, Path(gt=0, le=POSTGRES_INT4_MAX)],
    user: CurrentUser,
    body: FullbodyPromptRequest = Body(default_factory=FullbodyPromptRequest),
) -> ImagePromptResponse:
    """自备图提示词：按种子参考图锚定组装，种子缺失与 AI 路径同样失败，不做生图。"""
    try:
        prompt = await prepare_fullbody_prompt(
            user_id=user.id,
            avatar_id=avatar_id,
            feedback=body.feedback,
        )
    except (AvatarGenerationError, VisualReasoningError) as exc:
        raise _avatar_request_error(exc)
    except MissingLlmConfigError as exc:
        logger.warning("fullbody prompt missing config", extra={"user_id": user.id, "error": str(exc)})
        raise missing_config_error() from exc
    return ImagePromptResponse(prompt=prompt)


@router.post(
    "/avatar/{avatar_id}/fullbody/reference/adopt",
    response_model=AvatarAssetResponse | FullbodyCandidateResponse,
    status_code=status.HTTP_201_CREATED,
    openapi_extra=operation_policy(upload="image", wait_for_model=True),
)
@limiter.limit(lambda: f"{SETTINGS.companion_avatar_generate_rate_limit_per_minute}/minute")
async def post_fullbody_adopt(
    request: Request,
    avatar_id: Annotated[int, Path(gt=0, le=POSTGRES_INT4_MAX)],
    body: FullbodyAdoptRequest,
    user: CurrentUser,
) -> AvatarAssetResponse | FullbodyCandidateResponse:
    """自备图采纳：用户外部生成的图像按对应种子生成成功的语义落库。"""
    raw, content_type = await _decode_upload_image(body.image, body.content_type)
    try:
        asset = await adopt_fullbody_seed_view(
            user_id=user.id,
            avatar_id=avatar_id,
            data=raw,
            content_type=content_type,
        )
    except (AvatarGenerationError, VisualReasoningError) as exc:
        raise _avatar_request_error(exc)
    return asset


def _outfit_request_error(exc: OutfitError | VisualReasoningError) -> RequestError:
    if isinstance(exc, VisualReasoningError):
        return RequestError(status_code=502, message=str(exc), code="generation_failed")
    if isinstance(exc, OutfitNotFoundError):
        return RequestError(status_code=404, message="找不到对应的外观", details={"reason": str(exc)})
    if isinstance(exc, OutfitDraftExpiredError):
        return RequestError(status_code=409, message=str(exc), code="draft_expired")
    if isinstance(exc, OutfitStateError):
        return RequestError(status_code=409, message=str(exc), code="invalid_state")
    return RequestError(status_code=400, message=str(exc), code="invalid_request")


@router.get(
    "/outfits",
    response_model=OutfitListResponse,
    openapi_extra=operation_policy(identities=("desktop", "remote")),
)
async def get_outfits(user: CurrentUser, db: DbSession) -> OutfitListResponse:
    outfits = await list_outfits(db, user.id)
    return OutfitListResponse(outfits=outfits, policy=await get_outfit_policy(db, user.id))


@router.patch(
    "/outfits/policy",
    response_model=OutfitPolicyResponse,
    openapi_extra=operation_policy(identities=("desktop", "remote")),
)
async def patch_outfit_policy(body: OutfitPolicyRequest, user: CurrentUser, db: DbSession) -> OutfitPolicyResponse:
    return OutfitPolicyResponse(policy=await set_outfit_policy(db, user.id, body.policy))


# 换装路由不检查形象锁定：服装/发型是可换元素而非身份变更（见 DESIGN「身份、穿着与动态能力」）
@router.post(
    "/outfits",
    response_model=OutfitResponse,
    status_code=status.HTTP_201_CREATED,
    openapi_extra=operation_policy(identities=("desktop", "remote"), upload="image", wait_for_model=True),
)
async def post_outfit(
    body: Annotated[OutfitCreateRequest | OutfitAdoptRequest, Body(discriminator="source")],
    user: CurrentUser,
    db: DbSession,
) -> OutfitResponse:
    try:
        if isinstance(body, OutfitAdoptRequest):
            raw, content_type = await _decode_upload_image(body.image, body.content_type)
            return await adopt_outfit_draft_image_view(
                db,
                user.id,
                description=body.description,
                data=raw,
                content_type=content_type,
            )
        raw, content_type = await _decode_optional_image(body.image, body.content_type)
        outfit = await create_outfit_draft_view(
            db,
            user.id,
            description=body.description,
            image=raw,
            content_type=content_type,
        )
    except (OutfitError, VisualReasoningError) as exc:
        raise _outfit_request_error(exc)
    except AvatarGenerationError as exc:
        logger.warning(
            "outfit draft generation failed",
            extra={"user_id": user.id, "error": exc.internal},
        )
        raise RequestError(
            status_code=502,
            message=str(exc),
            code="generation_failed",
        ) from exc
    return outfit


@router.post(
    "/outfits/prompt",
    response_model=ImagePromptResponse,
    openapi_extra=operation_policy(identities=("desktop", "remote"), upload="image", wait_for_model=True),
)
async def post_outfit_prompt(
    body: OutfitPromptRequest,
    user: CurrentUser,
    db: DbSession,
) -> ImagePromptResponse:
    if body.outfit_id is not None:
        try:
            prompt = await prepare_outfit_regenerate_prompt(db, user.id, body.outfit_id, feedback=body.feedback)
        except (OutfitError, VisualReasoningError) as exc:
            raise _outfit_request_error(exc) from exc
        except AvatarGenerationError as exc:
            raise RequestError(status_code=502, message=str(exc), code="generation_failed") from exc
        return ImagePromptResponse(prompt=prompt)
    raw, content_type = await _decode_optional_image(body.image, body.content_type)
    try:
        prompt = await prepare_outfit_prompt(
            db,
            user.id,
            description=body.description,
            image=raw,
            content_type=content_type,
        )
    except (OutfitError, VisualReasoningError) as exc:
        raise _outfit_request_error(exc)
    except AvatarGenerationError as exc:
        logger.warning(
            "outfit prompt failed",
            extra={"user_id": user.id, "error": exc.internal},
        )
        raise RequestError(status_code=502, message=str(exc), code="generation_failed")
    return ImagePromptResponse(prompt=prompt)


@router.post(
    "/outfits/{outfit_id}/image",
    response_model=OutfitResponse,
    openapi_extra=operation_policy(identities=("desktop", "remote"), upload="image", wait_for_model=True),
)
async def post_outfit_regenerate(
    outfit_id: Annotated[int, Path(gt=0, le=POSTGRES_INT4_MAX)],
    body: Annotated[OutfitRegenerateRequest | OutfitImageImportRequest, Body(discriminator="source")],
    user: CurrentUser,
    db: DbSession,
) -> OutfitResponse:
    try:
        if isinstance(body, OutfitImageImportRequest):
            data, content_type = await _decode_upload_image(body.image, body.content_type)
            return await adopt_outfit_regenerate_image_view(
                db,
                user.id,
                outfit_id,
                data=data,
                content_type=content_type,
            )
        outfit = await regenerate_outfit_draft_view(db, user.id, outfit_id, feedback=body.feedback, mode=body.mode)
    except (OutfitError, VisualReasoningError) as exc:
        raise _outfit_request_error(exc)
    except AvatarGenerationError as exc:
        # AvatarGenerationError 的 str 按契约是公开文案（含编辑能力缺失等可行动指引），透传不替换。
        logger.warning(
            "outfit draft regenerate failed",
            extra={"user_id": user.id, "outfit_id": outfit_id, "error": exc.internal},
        )
        raise RequestError(status_code=502, message=str(exc), code="generation_failed")
    return outfit


@router.post(
    "/outfits/{outfit_id}/confirm",
    response_model=OutfitResponse,
    openapi_extra=operation_policy(identities=("desktop", "remote"), wait_for_model=True),
)
async def post_outfit_confirm(
    outfit_id: Annotated[int, Path(gt=0, le=POSTGRES_INT4_MAX)],
    user: CurrentUser,
    db: DbSession,
    body: OutfitConfirmRequest = Body(default_factory=OutfitConfirmRequest),
) -> OutfitResponse:
    """确认入柜：外观立绘转正为持久参考图（ready）。与同模块其他 POST 一致：以可缺省的空模型收 body，空对象不触发 422。"""
    try:
        outfit = await confirm_outfit_view(db, user.id, outfit_id)
    except OutfitError as exc:
        raise _outfit_request_error(exc)
    return outfit


@router.post(
    "/outfits/{outfit_id}/description/retry",
    response_model=OutfitResponse,
    openapi_extra=operation_policy(identities=("desktop", "remote"), wait_for_model=True),
)
async def post_retry_outfit_description(
    outfit_id: Annotated[int, Path(gt=0, le=POSTGRES_INT4_MAX)],
    user: CurrentUser,
    db: DbSession,
) -> OutfitResponse:
    try:
        outfit = await retry_outfit_description_view(db, user.id, outfit_id)
    except OutfitError as exc:
        raise _outfit_request_error(exc) from exc
    return outfit


@router.delete(
    "/outfits/{outfit_id}",
    response_model=CompanionOperationResponse,
    openapi_extra=operation_policy(identities=("desktop", "remote")),
)
async def delete_outfit_route(
    outfit_id: Annotated[int, Path(gt=0, le=POSTGRES_INT4_MAX)],
    user: CurrentUser,
    db: DbSession,
) -> CompanionOperationResponse:
    try:
        await delete_outfit(db, user.id, outfit_id)
    except OutfitError as exc:
        raise _outfit_request_error(exc)
    return CompanionOperationResponse(ok=True)


def _video_pack_request_error(exc: VideoPackError) -> RequestError:
    if isinstance(exc, VideoPackNotFoundError):
        return RequestError(status_code=404, message=str(exc), code="not_found")
    if isinstance(exc, VideoPackStateError):
        return RequestError(status_code=409, message=str(exc), code="invalid_state")
    return RequestError(status_code=400, message=str(exc), code="invalid_request")


_CLIP_MIME_BY_EXT = {
    "png": "image/png",
    "webp": "image/webp",
    "webm": "video/webm",
    "mp4": "video/mp4",
    "mov": "video/quicktime",
    "mkv": "video/x-matroska",
}


def _decode_clip_upload(data_b64: str, content_type: str | None, media_type: str) -> tuple[bytes, str]:
    """解码动作媒体并核对声明类型与文件格式；像素及透明门禁由处理入口负责。"""
    normalized = content_type.split(";")[0].strip().lower() if content_type else None
    if normalized is not None and (
        normalized not in _CLIP_MIME_BY_EXT.values() or not normalized.startswith(f"{media_type}/")
    ):
        raise RequestError(status_code=415, message="动作媒体类型与格式不符")
    try:
        raw = base64.b64decode(data_b64, validate=True)
    except ValueError:
        raise RequestError(status_code=400, message="片段数据无效")
    if not raw:
        raise RequestError(status_code=400, message="片段数据为空")
    ext = sniff_media_ext(raw)
    detected = _CLIP_MIME_BY_EXT.get(ext)
    if detected is None or not detected.startswith(f"{media_type}/"):
        raise RequestError(status_code=415, message="动作文件格式与媒体类型不符")
    normalized = normalized or detected
    # EBML 签名同时用于 WebM 与 Matroska，后处理按实际容器解码。
    if normalized != detected and not (ext == "webm" and normalized == "video/x-matroska"):
        raise RequestError(status_code=415, message="动作文件格式与媒体类型不符")
    return raw, normalized


@router.get(
    "/video-packs",
    response_model=VideoPackListResponse,
    openapi_extra=operation_policy(identities=("desktop", "remote")),
)
async def get_video_packs(user: CurrentUser, db: DbSession) -> VideoPackListResponse:
    return VideoPackListResponse(packs=await list_pack_responses(db, user.id))


@router.post(
    "/video-packs",
    response_model=VideoPackResponse,
    status_code=status.HTTP_201_CREATED,
    openapi_extra=operation_policy(identities=("desktop", "remote"), upload="action_pack"),
)
async def post_video_pack(
    body: Annotated[VideoPackCreateRequest | VideoPackGenerateRequest, Body(discriminator="source")],
    user: CurrentUser,
    db: DbSession,
) -> VideoPackResponse:
    if isinstance(body, VideoPackGenerateRequest):
        try:
            return await create_pack_from_reference_view(
                db,
                user.id,
                outfit_id=body.outfit_id,
                force=body.force,
                source_pack_id=body.source_pack_id,
                action=body.action,
                feedback=body.feedback,
            )
        except CharacterCardNotReadyError as exc:
            raise RequestError(status_code=409, message=str(exc)) from exc
        except VideoPackError as exc:
            raise _video_pack_request_error(exc) from exc
    clips: dict[str, tuple[bytes, str]] = {}
    ranges: dict[str, tuple[float, float]] = {}
    for clip in body.clips:
        if clip.action in clips:
            raise RequestError(status_code=400, message=f"动作片段重复：{clip.action}")
        raw, content_type = await asyncio.to_thread(_decode_clip_upload, clip.data, clip.content_type, clip.media_type)
        clips[clip.action] = (raw, content_type)
        if clip.media_type == "video" and clip.start_seconds is not None and clip.end_seconds is not None:
            ranges[clip.action] = (clip.start_seconds, clip.end_seconds)
    try:
        pack = await create_pack_from_clips_view(
            db,
            user.id,
            outfit_id=body.outfit_id,
            clips=clips,
            canvas=(body.canvas_width, body.canvas_height),
            action_ranges=ranges,
        )
    except VideoPackError as exc:
        raise _video_pack_request_error(exc)
    return pack


@router.post(
    "/video-packs/{pack_id}/production",
    response_model=VideoPackResponse,
    openapi_extra=operation_policy(identities=("desktop", "remote")),
)
async def produce_video_pack(
    pack_id: Annotated[int, Path(gt=0, le=POSTGRES_INT4_MAX)],
    body: Annotated[VideoPackEnsureRequest | VideoPackRetryRequest, Body(discriminator="command")],
    user: CurrentUser,
    db: DbSession,
) -> VideoPackResponse:
    try:
        if isinstance(body, VideoPackEnsureRequest):
            return await ensure_system_action_view(db, user.id, pack_id, body.action)
        return await retry_pack_view(db, user.id, pack_id, action=body.action)
    except VideoPackError as exc:
        raise _video_pack_request_error(exc) from exc


@router.put(
    "/video-packs/current",
    response_model=VideoPackResponse,
    openapi_extra=operation_policy(identities=("desktop", "remote")),
)
async def put_video_pack_selection(
    body: VideoPackSelectionRequest,
    user: CurrentUser,
    db: DbSession,
) -> VideoPackResponse:
    try:
        return await activate_pack_view(db, user.id, body.pack_id)
    except VideoPackError as exc:
        raise _video_pack_request_error(exc) from exc


@router.delete(
    "/video-packs/{pack_id}",
    response_model=CompanionOperationResponse,
    openapi_extra=operation_policy(identities=("desktop", "remote")),
)
async def delete_video_pack_route(
    pack_id: Annotated[int, Path(gt=0, le=POSTGRES_INT4_MAX)],
    user: CurrentUser,
    db: DbSession,
) -> CompanionOperationResponse:
    try:
        await delete_video_pack(db, user.id, pack_id)
    except VideoPackError as exc:
        raise _video_pack_request_error(exc)
    return CompanionOperationResponse(ok=True)


# 文件端点按会话或签名放行，不依赖 CurrentUser。
@router.get(
    "/asset/{user_id}/{asset_path:path}",
    openapi_extra=operation_policy(identities=("desktop", "remote", "public")),
)
async def serve_companion_asset(
    request: Request,
    user_id: Annotated[int, Path(gt=0, le=POSTGRES_INT4_MAX)],
    asset_path: str,
    session: OptionalSession,
    expires: int | None = None,
    sig: str | None = None,
) -> Response:
    is_authed = session is not None and (session[0].id == user_id)
    if not is_authed and not verify_signed_asset_request(user_id, asset_path, expires, sig):
        raise HTTPException(status_code=403, detail="Invalid or expired signature")
    result = resolve_companion_asset_path(user_id, asset_path)
    if result is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    path, content_type = result
    return await serve_ranged_file(request, path, content_type)
