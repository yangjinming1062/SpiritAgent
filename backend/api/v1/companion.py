import base64
import json

from common import get_router
from components import SESSION_LOCAL, SETTINGS, DbSession, get_logger, safe_json_loads
from fastapi import Body, HTTPException, Request, Response, status
from modules.auth import CurrentUser, OptionalSession
from modules.companion import (
    AvatarAssetResponse,
    AvatarFromImageRequest,
    AvatarGenerateRequest,
    AvatarHistoryResponse,
    AvatarPromptRequest,
    CharacterCardExtract,
    CharacterCardResponse,
    CharacterCardUpdate,
    CompanionMediaReview,
    CompanionOperationResponse,
    FullbodyAdoptRequest,
    FullbodyCandidateResponse,
    FullbodyConfirmRequest,
    FullbodyPromptRequest,
    FullbodyReferenceGenerateRequest,
    ImageAdoptRequest,
    ImagePromptResponse,
    MediaReviewResponse,
    OnboardingStateResponse,
    OutfitAdoptRequest,
    OutfitConfirmRequest,
    OutfitCreateRequest,
    OutfitListResponse,
    OutfitPolicyRequest,
    OutfitPolicyResponse,
    OutfitPromptRequest,
    OutfitRegeneratePromptRequest,
    OutfitRegenerateRequest,
    OutfitResponse,
    PersonaResponse,
    PersonaUpdate,
    PortraitConfirmRequest,
    VideoPackCreateRequest,
    VideoPackGenerateRequest,
    VideoPackListResponse,
    VideoPackResponse,
)
from pydantic import ValidationError
from services.adapters.http import limiter
from services.application.generation import (
    ALLOWED_AVATAR_UPLOAD_MIME_TYPES,
    AvatarGenerationError,
    AvatarNotFoundError,
    AvatarSourceUnreadableError,
    FullbodyGenerationError,
    ImageSealedError,
    MediaReviewStateError,
    OutfitDraftExpiredError,
    OutfitError,
    OutfitNotFoundError,
    OutfitStateError,
    VideoPackError,
    VideoPackNotFoundError,
    VideoPackStateError,
    accept_fullbody_candidate,
    accept_media_review,
    activate_outfit,
    activate_video_pack,
    adopt_avatar_seed,
    adopt_fullbody_seed,
    adopt_outfit_draft_image,
    adopt_outfit_regenerate_image,
    avatar_response,
    confirm_fullbody_seed,
    confirm_outfit,
    create_outfit_draft,
    create_video_pack_from_clips,
    create_video_pack_from_reference,
    delete_outfit,
    delete_video_pack,
    finalize_avatar,
    generate_avatar,
    generate_fullbody_reference,
    get_active_avatar,
    get_avatar_job_lock,
    get_media_review,
    get_outfit_policy,
    latest_fullbody_candidate,
    list_avatar_history,
    list_outfits,
    list_pack_responses,
    list_pending_media_reviews,
    outfit_response,
    prepare_avatar_prompt,
    prepare_fullbody_prompt,
    prepare_outfit_prompt,
    prepare_outfit_regenerate_prompt,
    regenerate_avatar_from_image,
    regenerate_outfit_draft,
    reject_media_review,
    resolve_uploaded_avatar_path,
    retry_fullbody_candidate_analysis,
    retry_video_pack,
    schedule_character_extraction,
    select_avatar,
    set_outfit_policy,
)
from services.domains.companion import (
    CharacterCardConflictError,
    CharacterCardNotReadyError,
    PersonaValidationError,
    character_card_response,
    confirm_portrait,
    get_character_card,
    get_onboarding_state,
    get_or_create_persona,
    load_persona_definition,
    request_character_extraction,
    schedule_personality_tag_refresh,
    update_character_card,
    update_persona,
)
from services.infrastructure.assets import (
    client_asset_url,
    resolve_companion_asset_path,
    serve_ranged_file,
    verify_signed_asset_request,
    verify_signed_avatar_request,
)
from services.infrastructure.llm import LLMRuntimeError, MissingLlmConfigError, VisualReasoningError

router = get_router()
logger = get_logger(__name__)


def _media_review_response(row: CompanionMediaReview) -> MediaReviewResponse:
    return MediaReviewResponse(
        id=row.id,
        status=row.status,
        reason=row.reason,
        media_type=row.media_type,
        media_url=client_asset_url(row.media_url),
        title=str((row.publication or {}).get("title") or ""),
    )


@router.get("/media-reviews", response_model=list[MediaReviewResponse])
async def get_pending_visual_media_reviews(user: CurrentUser) -> list[MediaReviewResponse]:
    return [_media_review_response(row) for row in await list_pending_media_reviews(user.id)]


@router.get("/media-reviews/{review_id}", response_model=MediaReviewResponse)
async def get_visual_media_review(review_id: int, user: CurrentUser) -> MediaReviewResponse:
    row = await get_media_review(user.id, review_id)
    if row is None:
        raise HTTPException(status_code=404, detail={"error": "待确认动作不存在"})
    return _media_review_response(row)


@router.post("/media-reviews/{review_id}/accept", response_model=MediaReviewResponse)
async def post_visual_media_review_accept(review_id: int, user: CurrentUser) -> MediaReviewResponse:
    try:
        row = await accept_media_review(user.id, review_id)
    except MediaReviewStateError as exc:
        raise HTTPException(status_code=409, detail={"error": str(exc)}) from exc
    if row is None:
        raise HTTPException(status_code=404, detail={"error": "待确认动作不存在"})
    if row.status == "rejected":
        raise HTTPException(status_code=409, detail={"error": "该媒体已被拒绝"})
    return _media_review_response(row)


@router.post("/media-reviews/{review_id}/reject", response_model=MediaReviewResponse)
async def post_visual_media_review_reject(review_id: int, user: CurrentUser) -> MediaReviewResponse:
    row = await reject_media_review(user.id, review_id)
    if row is None:
        raise HTTPException(status_code=404, detail={"error": "待确认动作不存在"})
    if row.status == "accepted":
        raise HTTPException(status_code=409, detail={"error": "该媒体已被采纳"})
    return _media_review_response(row)


@router.get("/onboarding/state", response_model=OnboardingStateResponse)
async def get_onboarding_state_route(
    user: CurrentUser,
    db: DbSession,
) -> OnboardingStateResponse:
    result = await get_onboarding_state(db, user.id)
    return OnboardingStateResponse(**result)


@router.get("/character-card", response_model=CharacterCardResponse | None)
async def get_character_card_route(user: CurrentUser, db: DbSession) -> CharacterCardResponse | None:
    card = await get_character_card(db, user.id)
    return character_card_response(card) if card is not None else None


@router.patch("/character-card", response_model=CharacterCardResponse)
async def patch_character_card_route(
    body: CharacterCardUpdate,
    user: CurrentUser,
    db: DbSession,
) -> CharacterCardResponse:
    try:
        return await update_character_card(db, user.id, body)
    except (CharacterCardConflictError, CharacterCardNotReadyError) as exc:
        raise HTTPException(status_code=409, detail={"error": str(exc)}) from exc


@router.post("/character-card/extract", response_model=CharacterCardResponse)
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
        raise HTTPException(status_code=409, detail={"error": str(exc)}) from exc
    schedule_character_extraction(user.id)
    return result


@router.get("/persona", response_model=PersonaResponse)
async def get_persona(user: CurrentUser, db: DbSession) -> PersonaResponse:
    persona = await get_or_create_persona(db, user.id)
    tags = safe_json_loads(persona.personality_tags_json or "[]", default=[])
    return PersonaResponse(
        is_complete=persona.is_complete,
        definition_json=json.dumps(load_persona_definition(persona), ensure_ascii=False),
        personality_tags=tags if isinstance(tags, list) else [],
        current_mood=persona.current_mood,
    )


@router.put("/persona", response_model=PersonaResponse)
async def put_persona(body: PersonaUpdate, user: CurrentUser, db: DbSession) -> PersonaResponse:
    data = safe_json_loads(body.definition_json, default={})
    try:
        persona = await update_persona(db, user.id, data)
    except PersonaValidationError as exc:
        raise HTTPException(status_code=422, detail={"error": "Persona validation error", "reason": str(exc)})
    # 延迟调度标签 LLM 抽取；同步执行会阻塞 PUT 超过 renderer 的 15s socket 超时，导致 onboarding 阶段后续 POST /avatar 无法触发。
    schedule_personality_tag_refresh(persona.id, user.id)
    tags = safe_json_loads(persona.personality_tags_json or "[]", default=[])
    return PersonaResponse(
        is_complete=persona.is_complete,
        definition_json=persona.definition_json,
        personality_tags=tags if isinstance(tags, list) else [],
        current_mood=persona.current_mood,
    )


@router.post("/portrait/confirm", response_model=CompanionOperationResponse)
async def post_portrait_confirm(
    body: PortraitConfirmRequest,
    user: CurrentUser,
    db: DbSession,
) -> CompanionOperationResponse:
    try:
        async with get_avatar_job_lock(user.id):
            previewed = await get_active_avatar(db, user.id)
            if previewed is None:
                raise HTTPException(status_code=404, detail={"error": "请先生成或上传头像"})
            if previewed.id != body.expected_avatar_id:
                raise HTTPException(status_code=409, detail={"error": "当前头像已更新，请重新加载预览后确认"})
            asset = await finalize_avatar(db, user.id)
            if asset is None:
                raise HTTPException(status_code=404, detail={"error": "请先生成或上传头像"})
            await confirm_portrait(db, user.id)
    except AvatarSourceUnreadableError as exc:
        raise HTTPException(status_code=409, detail={"error": "形象草稿已过期，请重新生成头像", "reason": str(exc)})
    return CompanionOperationResponse(ok=True)


@router.get("/avatar", response_model=AvatarAssetResponse)
async def get_avatar(user: CurrentUser, db: DbSession) -> AvatarAssetResponse:
    asset = await get_active_avatar(db, user.id)
    if asset is None:
        raise HTTPException(status_code=404, detail="No avatar found")
    # get_active_avatar 读时已重签 asset_url，此处禁止再次重签。
    return avatar_response(asset)


@router.post("/avatar", response_model=AvatarAssetResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit(lambda: f"{SETTINGS.companion_avatar_generate_rate_limit_per_minute}/minute")
async def post_avatar(
    request: Request,  # required by @limiter.limit
    user: CurrentUser,
    body: AvatarGenerateRequest = Body(default_factory=AvatarGenerateRequest),
) -> AvatarAssetResponse:
    async with SESSION_LOCAL() as pre_db:
        persona = await get_or_create_persona(pre_db, user.id)
        if not persona.is_complete:
            raise HTTPException(
                status_code=409,
                detail={"error": "请先完成 onboarding 再生成形象", "reason": "persona is incomplete"},
            )
    try:
        async with get_avatar_job_lock(user.id):
            asset = await generate_avatar(user_id=user.id, persona=persona, feedback=body.feedback)
    except ImageSealedError as exc:
        raise HTTPException(status_code=409, detail={"error": "形象已确认锁定，无法重新生成", "reason": str(exc)})
    except AvatarGenerationError as exc:
        err_detail = getattr(exc, "internal", str(exc))
        logger.warning("post_avatar generation failed", extra={"user_id": user.id, "error": err_detail})
        raise HTTPException(status_code=502, detail={"error": "伙伴形象生成失败，请稍后重试", "reason": str(exc)})
    except MissingLlmConfigError as exc:
        logger.warning("post_avatar missing config", extra={"user_id": user.id, "error": str(exc)})
        raise HTTPException(
            status_code=502,
            detail={"error": "LLM provider 未配置，请先在设置中配置 chat provider", "reason": str(exc)},
        )
    return avatar_response(asset)


def _decode_upload_image(image_b64: str | None, content_type: str | None) -> tuple[bytes | None, str | None]:
    """不支持的 MIME 抛 415；base64 损坏抛 400。"""
    if not image_b64:
        return None, None
    normalized = (content_type or "image/png").split(";")[0].strip().lower()
    if normalized not in ALLOWED_AVATAR_UPLOAD_MIME_TYPES:
        raise HTTPException(status_code=415, detail={"error": "仅支持 PNG / JPEG / WebP / GIF 图片"})
    try:
        return base64.b64decode(image_b64, validate=True), normalized
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid base64 image data")


@router.post("/avatar/from-image", response_model=AvatarAssetResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit(lambda: f"{SETTINGS.companion_avatar_generate_rate_limit_per_minute}/minute")
async def post_avatar_from_image(
    request: Request,  # required by @limiter.limit
    user: CurrentUser,
    body: AvatarFromImageRequest,
) -> AvatarAssetResponse:
    raw, content_type = _decode_upload_image(body.image, body.content_type)
    pres_raw, pres_content_type = _decode_upload_image(body.presentation_image, body.presentation_content_type)
    async with SESSION_LOCAL() as pre_db:
        persona = await get_or_create_persona(pre_db, user.id)
        if not persona.is_complete:
            raise HTTPException(
                status_code=409,
                detail={"error": "请先完成 onboarding 再基于图片生成形象", "reason": "persona is incomplete"},
            )
    try:
        async with get_avatar_job_lock(user.id):
            asset = await regenerate_avatar_from_image(
                user_id=user.id,
                persona=persona,
                data=raw,
                content_type=content_type,
                description=body.description,
                presentation_data=pres_raw,
                presentation_content_type=pres_content_type,
            )
    except ImageSealedError as exc:
        raise HTTPException(status_code=409, detail={"error": "形象已确认锁定，无法重新生成", "reason": str(exc)})
    except AvatarGenerationError as exc:
        err_detail = getattr(exc, "internal", str(exc))
        logger.warning("post_avatar_from_image failed", extra={"user_id": user.id, "error": err_detail})
        raise HTTPException(status_code=502, detail={"error": "按参考重绘失败，请稍后重试", "reason": str(exc)})
    except MissingLlmConfigError as exc:
        logger.warning("post_avatar_from_image missing config", extra={"user_id": user.id, "error": str(exc)})
        raise HTTPException(
            status_code=502,
            detail={"error": "LLM provider 未配置，请先在设置中配置 chat provider", "reason": str(exc)},
        )

    return avatar_response(asset)


@router.post("/avatar/prompt", response_model=ImagePromptResponse)
@limiter.limit(lambda: f"{SETTINGS.companion_avatar_generate_rate_limit_per_minute}/minute")
async def post_avatar_prompt(request: Request, body: AvatarPromptRequest, user: CurrentUser) -> ImagePromptResponse:
    try:
        prompt = await prepare_avatar_prompt(user.id, feedback=body.feedback, has_reference=body.has_reference)
    except AvatarGenerationError as exc:
        raise _avatar_http_error(exc)
    except MissingLlmConfigError:
        raise HTTPException(status_code=502, detail={"error": "提示词服务未配置，仍可直接上传准备好的图片"})
    except (LLMRuntimeError, RuntimeError, ValidationError):
        logger.warning("avatar prompt failed", extra={"user_id": user.id}, exc_info=True)
        raise HTTPException(
            status_code=502,
            detail={"error": "提示词暂时无法生成，请稍后重试；仍可直接上传准备好的图片"},
        )
    return ImagePromptResponse(prompt=prompt)


@router.post("/avatar/adopt", response_model=AvatarAssetResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit(lambda: f"{SETTINGS.companion_avatar_generate_rate_limit_per_minute}/minute")
async def post_avatar_adopt(request: Request, body: FullbodyAdoptRequest, user: CurrentUser) -> AvatarAssetResponse:
    raw, content_type = _decode_upload_image(body.image, body.content_type)
    if not raw:
        raise HTTPException(status_code=400, detail={"error": "请选择有效的图片"})
    try:
        async with get_avatar_job_lock(user.id):
            asset = await adopt_avatar_seed(user_id=user.id, data=raw, content_type=content_type or "image/png")
    except AvatarGenerationError as exc:
        raise _avatar_http_error(exc)
    return avatar_response(asset)


@router.get("/avatar/history", response_model=AvatarHistoryResponse)
async def get_avatar_history(user: CurrentUser, db: DbSession) -> AvatarHistoryResponse:
    history = await list_avatar_history(db, user.id)
    return AvatarHistoryResponse(history=[avatar_response(a) for a in history])


@router.put("/avatar/{avatar_id}/select", response_model=AvatarAssetResponse)
async def put_avatar_select(avatar_id: int, user: CurrentUser, db: DbSession) -> AvatarAssetResponse:
    try:
        async with get_avatar_job_lock(user.id):
            asset = await select_avatar(db, user.id, avatar_id)
    except AvatarNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": "找不到对应的形象", "reason": str(exc)})
    except ImageSealedError as exc:
        raise HTTPException(status_code=409, detail={"error": "形象已确认锁定，无法重新生成", "reason": str(exc)})
    return avatar_response(asset)


@router.post("/avatar/{avatar_id}/fullbody/reference", response_model=AvatarAssetResponse | FullbodyCandidateResponse)
@limiter.limit(lambda: f"{SETTINGS.companion_avatar_generate_rate_limit_per_minute}/minute")
async def post_fullbody_reference(
    request: Request,
    avatar_id: int,
    body: FullbodyReferenceGenerateRequest,
    user: CurrentUser,
) -> AvatarAssetResponse | FullbodyCandidateResponse:
    raw, content_type = _decode_upload_image(body.image, body.content_type)
    try:
        asset = await generate_fullbody_reference(
            user.id,
            avatar_id=avatar_id,
            feedback=body.feedback,
            reference_image=base64.b64encode(raw).decode("utf-8") if raw else None,
            reference_content_type=content_type,
            mode=body.mode,
            candidate_id=body.candidate_id,
        )
    except AvatarNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": str(exc)})
    except AvatarSourceUnreadableError as exc:
        raise HTTPException(status_code=409, detail={"error": str(exc)})
    except CharacterCardNotReadyError as exc:
        raise HTTPException(status_code=409, detail={"error": str(exc)}) from exc
    except VisualReasoningError as exc:
        raise HTTPException(status_code=502, detail={"error": str(exc)})
    except FullbodyGenerationError as exc:
        # 供应商链失败包装层：str 已透传公开文案（如编辑能力缺失指引），502 语义是可重试失败。
        logger.warning("fullbody reference generation failed", extra={"user_id": user.id, "error": exc.internal})
        raise HTTPException(status_code=502, detail={"error": str(exc), "reason": str(exc)})
    except AvatarGenerationError as exc:
        # edit 守卫（反馈缺失、参考图同给等）是确定性的请求错误，公开文案直达用户。
        logger.warning("fullbody reference guard rejected", extra={"user_id": user.id, "error": exc.internal})
        raise HTTPException(status_code=400, detail={"error": str(exc)})
    except MissingLlmConfigError:
        raise HTTPException(status_code=502, detail={"error": "生成服务未配置，请先在设置中配置供应商"})
    return asset if isinstance(asset, FullbodyCandidateResponse) else avatar_response(asset)


@router.get("/avatar/{avatar_id}/fullbody/candidate", response_model=FullbodyCandidateResponse | None)
async def get_fullbody_candidate(avatar_id: int, user: CurrentUser) -> FullbodyCandidateResponse | None:
    return await latest_fullbody_candidate(user.id, avatar_id)


@router.post("/avatar/{avatar_id}/fullbody/candidate/{candidate_id}/analyze", response_model=FullbodyCandidateResponse)
async def post_fullbody_candidate_analyze(
    avatar_id: int,
    candidate_id: int,
    user: CurrentUser,
) -> FullbodyCandidateResponse:
    try:
        return await retry_fullbody_candidate_analysis(user.id, avatar_id, candidate_id)
    except AvatarNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": str(exc)}) from exc
    except AvatarGenerationError as exc:
        raise HTTPException(status_code=409, detail={"error": str(exc)}) from exc


@router.post("/avatar/{avatar_id}/fullbody/candidate/{candidate_id}/accept", response_model=AvatarAssetResponse)
async def post_fullbody_candidate_accept(
    avatar_id: int,
    candidate_id: int,
    user: CurrentUser,
) -> AvatarAssetResponse:
    try:
        asset = await accept_fullbody_candidate(user.id, avatar_id, candidate_id)
    except AvatarGenerationError as exc:
        raise _avatar_http_error(exc) from exc
    return avatar_response(asset)


@router.post("/avatar/{avatar_id}/fullbody/confirm", response_model=AvatarAssetResponse)
@limiter.limit(lambda: f"{SETTINGS.companion_avatar_generate_rate_limit_per_minute}/minute")
async def post_fullbody_confirm(
    request: Request,
    avatar_id: int,
    body: FullbodyConfirmRequest,
    user: CurrentUser,
) -> AvatarAssetResponse:
    try:
        asset = await confirm_fullbody_seed(user.id, avatar_id=avatar_id, expected_url=body.expected_url)
    except AvatarGenerationError as exc:
        raise _avatar_http_error(exc)
    schedule_character_extraction(user.id)
    return avatar_response(asset)


def _avatar_http_error(exc: AvatarGenerationError | VisualReasoningError) -> HTTPException:
    """自备图提示词/采纳端点共用的错误映射：语义与对应生成端点一致。"""
    if isinstance(exc, CharacterCardNotReadyError):
        return HTTPException(status_code=409, detail={"error": str(exc)})
    if isinstance(exc, VisualReasoningError):
        return HTTPException(status_code=502, detail={"error": str(exc)})
    if isinstance(exc, AvatarNotFoundError):
        return HTTPException(status_code=404, detail={"error": "找不到对应的形象", "reason": str(exc)})
    if isinstance(exc, ImageSealedError):
        return HTTPException(status_code=409, detail={"error": "形象已确认锁定，无法重新生成", "reason": str(exc)})
    if isinstance(exc, AvatarSourceUnreadableError):
        # 与 AI 生成端点一致：种子/头像文件不可读是需用户先修复源资产的状态冲突，不是普通请求错误。
        return HTTPException(status_code=409, detail={"error": str(exc)})
    return HTTPException(status_code=400, detail={"error": str(exc)})


@router.post("/avatar/{avatar_id}/fullbody/reference/prompt", response_model=ImagePromptResponse)
@limiter.limit(lambda: f"{SETTINGS.companion_avatar_generate_rate_limit_per_minute}/minute")
async def post_fullbody_prompt(
    request: Request,
    avatar_id: int,
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
        raise _avatar_http_error(exc)
    except MissingLlmConfigError as exc:
        logger.warning("fullbody prompt missing config", extra={"user_id": user.id, "error": str(exc)})
        raise HTTPException(
            status_code=502,
            detail={"error": "生成服务未配置，请先在设置中配置供应商", "reason": str(exc)},
        )
    return ImagePromptResponse(prompt=prompt)


@router.post(
    "/avatar/{avatar_id}/fullbody/reference/adopt",
    response_model=AvatarAssetResponse | FullbodyCandidateResponse,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit(lambda: f"{SETTINGS.companion_avatar_generate_rate_limit_per_minute}/minute")
async def post_fullbody_adopt(
    request: Request,
    avatar_id: int,
    body: FullbodyAdoptRequest,
    user: CurrentUser,
) -> AvatarAssetResponse | FullbodyCandidateResponse:
    """自备图采纳：用户外部生成的图像按对应种子生成成功的语义落库。"""
    raw, content_type = _decode_upload_image(body.image, body.content_type)
    if not raw:
        raise HTTPException(status_code=400, detail="Invalid image data")
    try:
        asset = await adopt_fullbody_seed(
            user_id=user.id,
            avatar_id=avatar_id,
            data=raw,
            content_type=content_type or "image/png",
        )
    except AvatarGenerationError as exc:
        raise _avatar_http_error(exc)
    except MissingLlmConfigError as exc:
        logger.warning("fullbody adopt missing config", extra={"user_id": user.id, "error": str(exc)})
        raise HTTPException(
            status_code=502,
            detail={"error": "生成服务未配置，请先在设置中配置供应商", "reason": str(exc)},
        )
    return asset if isinstance(asset, FullbodyCandidateResponse) else avatar_response(asset)


def _outfit_http_error(exc: OutfitError | VisualReasoningError) -> HTTPException:
    if isinstance(exc, VisualReasoningError):
        return HTTPException(status_code=502, detail={"error": str(exc), "reason": "generation_failed"})
    if isinstance(exc, OutfitNotFoundError):
        return HTTPException(status_code=404, detail={"error": "找不到对应的外观", "reason": str(exc)})
    if isinstance(exc, OutfitDraftExpiredError):
        return HTTPException(status_code=409, detail={"error": str(exc), "reason": "draft_expired"})
    if isinstance(exc, OutfitStateError):
        return HTTPException(status_code=409, detail={"error": str(exc), "reason": "invalid_state"})
    return HTTPException(status_code=400, detail={"error": str(exc), "reason": "invalid_request"})


@router.get("/outfits", response_model=OutfitListResponse)
async def get_outfits(user: CurrentUser, db: DbSession) -> OutfitListResponse:
    outfits = await list_outfits(db, user.id)
    return OutfitListResponse(outfits=outfits, policy=await get_outfit_policy(db, user.id))


@router.patch("/outfits/policy", response_model=OutfitPolicyResponse)
async def patch_outfit_policy(body: OutfitPolicyRequest, user: CurrentUser, db: DbSession) -> OutfitPolicyResponse:
    return OutfitPolicyResponse(policy=await set_outfit_policy(db, user.id, body.policy))


# 换装路由不检查形象锁定：服装/发型是可换元素而非身份变更（DESIGN §5.4 豁免，同背面种子先例）
@router.post("/outfits", response_model=OutfitResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit(lambda: f"{SETTINGS.companion_outfit_generate_rate_limit_per_hour}/hour")
async def post_outfit(
    request: Request,  # required by @limiter.limit
    body: OutfitCreateRequest,
    user: CurrentUser,
    db: DbSession,
) -> OutfitResponse:
    raw, content_type = _decode_upload_image(body.image, body.content_type)
    try:
        outfit = await create_outfit_draft(
            db,
            user.id,
            description=body.description,
            image=raw,
            content_type=content_type,
        )
    except (OutfitError, VisualReasoningError) as exc:
        raise _outfit_http_error(exc)
    except AvatarGenerationError as exc:
        logger.warning(
            "outfit draft generation failed",
            extra={"user_id": user.id, "error": getattr(exc, "internal", str(exc))},
        )
        raise HTTPException(
            status_code=502,
            detail={"error": "外观生成失败，请稍后重试", "reason": "generation_failed"},
        )
    return outfit_response(outfit)


@router.post("/outfits/prompt", response_model=ImagePromptResponse)
@limiter.limit(lambda: f"{SETTINGS.companion_outfit_generate_rate_limit_per_hour}/hour")
async def post_outfit_prompt(
    request: Request,  # required by @limiter.limit
    body: OutfitPromptRequest,
    user: CurrentUser,
    db: DbSession,
) -> ImagePromptResponse:
    """自备图提示词（创建语境）：整合链与创建草稿一致；服装参考整合失败只降级着装描述，
    身份仍由全身种子图锚定。"""
    raw, content_type = _decode_upload_image(body.image, body.content_type)
    try:
        prompt = await prepare_outfit_prompt(
            db,
            user.id,
            description=body.description,
            image=raw,
            content_type=content_type,
        )
    except (OutfitError, VisualReasoningError) as exc:
        raise _outfit_http_error(exc)
    except AvatarGenerationError as exc:
        logger.warning(
            "outfit prompt failed",
            extra={"user_id": user.id, "error": getattr(exc, "internal", str(exc))},
        )
        raise HTTPException(status_code=502, detail={"error": str(exc), "reason": "generation_failed"})
    return ImagePromptResponse(prompt=prompt)


@router.post("/outfits/adopt", response_model=OutfitResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit(lambda: f"{SETTINGS.companion_outfit_generate_rate_limit_per_hour}/hour")
async def post_outfit_adopt(
    request: Request,  # required by @limiter.limit
    body: OutfitAdoptRequest,
    user: CurrentUser,
    db: DbSession,
) -> OutfitResponse:
    """自备图采纳（创建语境）：外部生成的立绘按创建草稿语义入库。"""
    raw, content_type = _decode_upload_image(body.image, body.content_type)
    if not raw:
        raise HTTPException(status_code=400, detail="Invalid image data")
    try:
        outfit = await adopt_outfit_draft_image(
            db,
            user.id,
            description=body.description,
            data=raw,
            content_type=content_type,
        )
    except OutfitError as exc:
        raise _outfit_http_error(exc)
    return outfit_response(outfit)


@router.post("/outfits/{outfit_id}/regenerate", response_model=OutfitResponse)
@limiter.limit(lambda: f"{SETTINGS.companion_outfit_generate_rate_limit_per_hour}/hour")
async def post_outfit_regenerate(
    request: Request,  # required by @limiter.limit
    outfit_id: int,
    body: OutfitRegenerateRequest,
    user: CurrentUser,
    db: DbSession,
) -> OutfitResponse:
    try:
        outfit = await regenerate_outfit_draft(db, user.id, outfit_id, feedback=body.feedback, mode=body.mode)
    except (OutfitError, VisualReasoningError) as exc:
        raise _outfit_http_error(exc)
    except AvatarGenerationError as exc:
        # AvatarGenerationError 的 str 按契约是公开文案（含编辑能力缺失等可行动指引），透传不替换。
        logger.warning(
            "outfit draft regenerate failed",
            extra={"user_id": user.id, "outfit_id": outfit_id, "error": getattr(exc, "internal", str(exc))},
        )
        raise HTTPException(status_code=502, detail={"error": str(exc), "reason": "generation_failed"})
    return outfit_response(outfit)


@router.post("/outfits/{outfit_id}/prompt", response_model=ImagePromptResponse)
@limiter.limit(lambda: f"{SETTINGS.companion_outfit_generate_rate_limit_per_hour}/hour")
async def post_outfit_regenerate_prompt(
    request: Request,  # required by @limiter.limit
    outfit_id: int,
    body: OutfitRegeneratePromptRequest,
    user: CurrentUser,
    db: DbSession,
) -> ImagePromptResponse:
    """自备图提示词（草稿重绘语境）：反馈整合同草稿重绘，身份由全身种子图锚定。"""
    try:
        prompt = await prepare_outfit_regenerate_prompt(db, user.id, outfit_id, feedback=body.feedback)
    except (OutfitError, VisualReasoningError) as exc:
        raise _outfit_http_error(exc)
    except AvatarGenerationError as exc:
        logger.warning(
            "outfit regenerate prompt failed",
            extra={"user_id": user.id, "outfit_id": outfit_id, "error": getattr(exc, "internal", str(exc))},
        )
        raise HTTPException(status_code=502, detail={"error": str(exc), "reason": "generation_failed"})
    return ImagePromptResponse(prompt=prompt)


@router.post("/outfits/{outfit_id}/adopt", response_model=OutfitResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit(lambda: f"{SETTINGS.companion_outfit_generate_rate_limit_per_hour}/hour")
async def post_outfit_regenerate_adopt(
    request: Request,  # required by @limiter.limit
    outfit_id: int,
    body: ImageAdoptRequest,
    user: CurrentUser,
    db: DbSession,
) -> OutfitResponse:
    """自备图采纳（草稿重绘语境）：替换草稿/失败外观的立绘，状态回到草稿。"""
    raw, content_type = _decode_upload_image(body.image, body.content_type)
    if not raw:
        raise HTTPException(status_code=400, detail="Invalid image data")
    try:
        outfit = await adopt_outfit_regenerate_image(
            db,
            user.id,
            outfit_id,
            data=raw,
            content_type=content_type,
        )
    except OutfitError as exc:
        raise _outfit_http_error(exc)
    return outfit_response(outfit)


@router.post("/outfits/{outfit_id}/confirm", response_model=OutfitResponse)
async def post_outfit_confirm(
    outfit_id: int,
    user: CurrentUser,
    db: DbSession,
    body: OutfitConfirmRequest = Body(default_factory=OutfitConfirmRequest),
) -> OutfitResponse:
    """确认入柜：外观立绘转正为持久参考图（ready）。与同模块其他 POST 一致：
    以可缺省的空模型收 body，空对象不触发 422。"""
    try:
        outfit = await confirm_outfit(db, user.id, outfit_id)
    except OutfitError as exc:
        raise _outfit_http_error(exc)
    return outfit_response(outfit)


@router.put("/outfits/{outfit_id}/activate", response_model=OutfitResponse)
async def put_outfit_activate(
    outfit_id: int,
    user: CurrentUser,
    db: DbSession,
) -> OutfitResponse:
    try:
        outfit = await activate_outfit(db, user.id, outfit_id)
    except OutfitError as exc:
        raise _outfit_http_error(exc)
    return outfit_response(outfit)


@router.delete("/outfits/{outfit_id}", response_model=CompanionOperationResponse)
async def delete_outfit_route(
    outfit_id: int,
    user: CurrentUser,
    db: DbSession,
) -> CompanionOperationResponse:
    try:
        await delete_outfit(db, user.id, outfit_id)
    except OutfitError as exc:
        raise _outfit_http_error(exc)
    return CompanionOperationResponse(ok=True)


def _video_pack_http_error(exc: VideoPackError) -> HTTPException:
    if isinstance(exc, VideoPackNotFoundError):
        return HTTPException(status_code=404, detail={"error": str(exc), "reason": "not_found"})
    if isinstance(exc, VideoPackStateError):
        return HTTPException(status_code=409, detail={"error": str(exc), "reason": "invalid_state"})
    return HTTPException(status_code=400, detail={"error": str(exc), "reason": "invalid_request"})


_ALLOWED_CLIP_MIME_TYPES = {"video/webm", "video/mp4", "video/quicktime", "video/x-matroska"}


def _decode_clip_upload(data_b64: str, content_type: str | None) -> tuple[bytes, str]:
    """base64 片段解码；仅接受四种视频 MIME，损坏数据拒绝。"""
    normalized = (content_type or "video/webm").split(";")[0].strip().lower()
    if normalized not in _ALLOWED_CLIP_MIME_TYPES:
        raise HTTPException(status_code=415, detail={"error": "仅支持 WebM / MP4 / MOV / MKV 片段"})
    try:
        raw = base64.b64decode(data_b64, validate=True)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": "片段数据无效"})
    if not raw:
        raise HTTPException(status_code=400, detail={"error": "片段数据为空"})
    return raw, normalized


@router.get("/video-packs", response_model=VideoPackListResponse)
async def get_video_packs(user: CurrentUser, db: DbSession) -> VideoPackListResponse:
    return VideoPackListResponse(packs=[VideoPackResponse(**item) for item in await list_pack_responses(db, user.id)])


@router.post("/video-packs", response_model=VideoPackResponse, status_code=status.HTTP_201_CREATED)
async def post_video_pack(
    body: VideoPackCreateRequest,
    user: CurrentUser,
    db: DbSession,
) -> VideoPackResponse:
    """上传动作片段创建视频包：处理与发布在后台进行，结果经 companion.video 事件回流。"""
    clips: dict[str, tuple[bytes, str]] = {}
    ranges: dict[str, tuple[float, float]] = {}
    for clip in body.clips:
        if clip.action in clips:
            raise HTTPException(status_code=400, detail={"error": f"动作片段重复：{clip.action}"})
        raw, content_type = _decode_clip_upload(clip.data, clip.content_type)
        clips[clip.action] = (raw, content_type)
        if clip.start_seconds is not None and clip.end_seconds is not None:
            ranges[clip.action] = (clip.start_seconds, clip.end_seconds)
    try:
        pack = await create_video_pack_from_clips(
            db,
            user.id,
            outfit_id=body.outfit_id,
            clips=clips,
            canvas=(body.canvas_width, body.canvas_height),
            action_ranges=ranges,
        )
    except VideoPackError as exc:
        raise _video_pack_http_error(exc)
    return VideoPackResponse(
        id=pack.id,
        outfit_id=pack.outfit_id,
        pack_version=pack.pack_version,
        status=pack.status,
        active=pack.active,
    )


@router.post(
    "/video-packs/generate",
    response_model=VideoPackResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_video_pack_generate(
    body: VideoPackGenerateRequest,
    user: CurrentUser,
    db: DbSession,
) -> VideoPackResponse:
    """按参考生成视频包：逐动作脚本与关键帧 → 视频供应商链首尾帧短片 → 语义抠像与循环验收。
    生成与处理在后台进行，进度与结果经 companion.video.progress / ready / failed 事件回流。"""
    try:
        pack = await create_video_pack_from_reference(
            db,
            user.id,
            outfit_id=body.outfit_id,
            force=body.force,
            source_pack_id=body.source_pack_id,
            action=body.action,
            feedback=body.feedback,
        )
    except CharacterCardNotReadyError as exc:
        raise HTTPException(status_code=409, detail={"error": str(exc)}) from exc
    except VideoPackError as exc:
        raise _video_pack_http_error(exc)
    return VideoPackResponse(
        id=pack.id,
        outfit_id=pack.outfit_id,
        pack_version=pack.pack_version,
        status=pack.status,
        active=pack.active,
    )


@router.post("/video-packs/{pack_id}/retry", response_model=VideoPackResponse)
async def post_video_pack_retry(pack_id: int, user: CurrentUser, db: DbSession) -> VideoPackResponse:
    try:
        pack = await retry_video_pack(db, user.id, pack_id)
    except VideoPackError as exc:
        raise _video_pack_http_error(exc)
    return VideoPackResponse(
        id=pack.id,
        outfit_id=pack.outfit_id,
        pack_version=pack.pack_version,
        status=pack.status,
        active=pack.active,
    )


@router.put("/video-packs/{pack_id}/activate", response_model=VideoPackResponse)
async def put_video_pack_activate(pack_id: int, user: CurrentUser, db: DbSession) -> VideoPackResponse:
    try:
        pack = await activate_video_pack(db, user.id, pack_id)
    except VideoPackError as exc:
        raise _video_pack_http_error(exc)
    item = await list_pack_responses(db, user.id)
    for entry in item:
        if entry["id"] == pack.id:
            return VideoPackResponse(**entry)
    raise HTTPException(status_code=404, detail={"error": "视频包不存在"})


@router.delete("/video-packs/{pack_id}", response_model=CompanionOperationResponse)
async def delete_video_pack_route(pack_id: int, user: CurrentUser, db: DbSession) -> CompanionOperationResponse:
    try:
        await delete_video_pack(db, user.id, pack_id)
    except VideoPackError as exc:
        raise _video_pack_http_error(exc)
    return CompanionOperationResponse(ok=True)


public_router = get_router()


@public_router.get("/avatar/file/{filename}")
async def serve_avatar_file(
    request: Request,
    filename: str,
    session: OptionalSession,
    expires: int | None = None,
    sig: str | None = None,
) -> Response:
    if session is None and not verify_signed_avatar_request(filename, expires, sig):
        raise HTTPException(status_code=403, detail="Invalid or expired signature")
    result = resolve_uploaded_avatar_path(filename)
    if result is None:
        raise HTTPException(status_code=404, detail="Avatar not found")
    path, content_type = result
    return await serve_ranged_file(request, path, content_type)


@public_router.get("/asset/{user_id}/{filename:path}")
async def serve_companion_asset(
    request: Request,
    user_id: int,
    filename: str,
    session: OptionalSession,
    expires: int | None = None,
    sig: str | None = None,
) -> Response:
    is_authed = session is not None and (session[0].id == user_id)
    if not is_authed and not verify_signed_asset_request(user_id, filename, expires, sig):
        raise HTTPException(status_code=403, detail="Invalid or expired signature")
    result = resolve_companion_asset_path(user_id, filename)
    if result is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    path, content_type = result
    return await serve_ranged_file(request, path, content_type)
