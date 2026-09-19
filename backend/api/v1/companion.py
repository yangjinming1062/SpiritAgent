import base64
from typing import Literal

from common import get_router
from components import SESSION_LOCAL, SETTINGS, DbSession, get_logger, safe_json_loads
from fastapi import Body, HTTPException, Request, Response, status
from modules.auth import CurrentUser, OptionalSession
from modules.companion import (
    AvatarAssetResponse,
    AvatarFromImageRequest,
    AvatarGenerateRequest,
    AvatarHistoryResponse,
    AvatarUploadRequest,
    Companion2DModelResponse,
    Companion3DModelResponse,
    CompanionOperationResponse,
    Fullbody2dFrontGenerateRequest,
    Fullbody3dSeedGenerateRequest,
    FullbodyAdoptRequest,
    FullbodyConfirmFrontRequest,
    FullbodyPromptRequest,
    FullbodyReferenceGenerateRequest,
    FullbodySeedKind,
    ImageAdoptRequest,
    ImagePromptResponse,
    ModelGenerateRequest,
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
    RenderModeRequest,
)
from services.adapters.http import limiter
from services.application.generation import (
    ALLOWED_AVATAR_UPLOAD_MIME_TYPES,
    AvatarGenerationError,
    AvatarNotFoundError,
    AvatarSourceUnreadableError,
    FrontSeedMissingError,
    FullbodyGenerationError,
    ImageSealedError,
    Mesh2DNotReadyError,
    ModelGenerationError,
    ModelGenerationInProgressError,
    ModelProviderNotConfiguredError,
    OutfitDraftExpiredError,
    OutfitError,
    OutfitNotFoundError,
    OutfitStateError,
    SeedPromptMissingError,
    activate_outfit,
    adopt_fullbody_seed,
    adopt_outfit_draft_image,
    adopt_outfit_pose,
    adopt_outfit_regenerate_image,
    avatar_response,
    confirm_fullbody_front,
    confirm_outfit,
    create_outfit_draft,
    delete_outfit,
    finalize_avatar,
    generate_avatar,
    generate_companion_model,
    generate_fullbody_back,
    generate_fullbody_front_2d,
    generate_fullbody_front_3d,
    generate_fullbody_reference,
    generate_mesh2d_model,
    get_active_avatar,
    get_active_mesh2d_response,
    get_avatar_job_lock,
    get_outfit_policy,
    list_avatar_history,
    list_outfits,
    model_response,
    outfit_response,
    prepare_fullbody_prompt,
    prepare_outfit_prompt,
    prepare_outfit_regenerate_prompt,
    prepare_pose_prompt,
    regenerate_avatar_from_image,
    regenerate_outfit_draft,
    regenerate_outfit_pose,
    resolve_uploaded_avatar_path,
    schedule_initial_room,
    select_avatar,
    set_outfit_policy,
    set_render_mode,
    upload_avatar,
)
from services.domains.companion import (
    PersonaValidationError,
    confirm_portrait,
    get_active_model,
    get_onboarding_state,
    get_or_create_persona,
    schedule_personality_tag_refresh,
    update_persona,
)
from services.infrastructure.assets import (
    resolve_companion_asset_path,
    resolve_companion_model_path,
    serve_ranged_file,
    verify_signed_asset_request,
    verify_signed_avatar_request,
)
from services.infrastructure.llm import MissingLlmConfigError

router = get_router()

logger = get_logger(__name__)


@router.get("/onboarding/state", response_model=OnboardingStateResponse)
async def get_onboarding_state_route(
    user: CurrentUser,
    db: DbSession,
) -> OnboardingStateResponse:
    result = await get_onboarding_state(db, user.id)
    return OnboardingStateResponse(**result)


@router.get("/persona", response_model=PersonaResponse)
async def get_persona(user: CurrentUser, db: DbSession) -> PersonaResponse:
    persona = await get_or_create_persona(db, user.id)
    tags = safe_json_loads(persona.personality_tags_json or "[]", default=[])
    return PersonaResponse(
        is_complete=persona.is_complete,
        definition_json=persona.definition_json,
        personality_tags=tags if isinstance(tags, list) else [],
        render_mode=persona.render_mode or "2d",
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
        render_mode=persona.render_mode or "2d",
        current_mood=persona.current_mood,
    )


@router.post("/portrait/confirm", response_model=CompanionOperationResponse)
async def post_portrait_confirm(
    user: CurrentUser,
    db: DbSession,
) -> CompanionOperationResponse:
    try:
        async with get_avatar_job_lock(user.id):
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
            asset = await generate_avatar(user_id=user.id, persona=persona)
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


@router.post("/avatar/upload", response_model=AvatarAssetResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit(lambda: f"{SETTINGS.companion_avatar_generate_rate_limit_per_minute}/minute")
async def post_avatar_upload(
    request: Request,  # required by @limiter.limit
    user: CurrentUser,
    body: AvatarUploadRequest,
) -> AvatarAssetResponse:
    raw, content_type = _decode_upload_image(body.image, body.content_type)
    if not raw:
        raise HTTPException(status_code=400, detail="Invalid image data")
    async with SESSION_LOCAL() as pre_db:
        persona = await get_or_create_persona(pre_db, user.id)
        if not persona.is_complete:
            raise HTTPException(
                status_code=409,
                detail={"error": "请先完成 onboarding 再上传形象", "reason": "persona is incomplete"},
            )
    try:
        async with get_avatar_job_lock(user.id):
            asset = await upload_avatar(
                user_id=user.id,
                persona=persona,
                data=raw,
                content_type=content_type or "image/png",
            )
    except ImageSealedError as exc:
        raise HTTPException(status_code=409, detail={"error": "形象已确认锁定，无法重新生成", "reason": str(exc)})
    except Exception as exc:
        logger.warning("post_avatar_upload failed", extra={"user_id": user.id, "error": str(exc)})
        raise HTTPException(status_code=500, detail={"error": "上传头像失败，请稍后重试", "reason": str(exc)})

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


@router.post("/avatar/{avatar_id}/fullbody/reference", response_model=AvatarAssetResponse)
@limiter.limit(lambda: f"{SETTINGS.companion_avatar_generate_rate_limit_per_minute}/minute")
async def post_fullbody_reference(
    request: Request,
    avatar_id: int,
    body: FullbodyReferenceGenerateRequest,
    user: CurrentUser,
) -> AvatarAssetResponse:
    raw, content_type = _decode_upload_image(body.image, body.content_type)
    try:
        asset = await generate_fullbody_reference(
            user.id,
            avatar_id=avatar_id,
            feedback=body.feedback,
            reference_image=base64.b64encode(raw).decode("utf-8") if raw else None,
            reference_content_type=content_type,
            mode=body.mode,
        )
    except AvatarNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": str(exc)})
    except AvatarSourceUnreadableError as exc:
        raise HTTPException(status_code=409, detail={"error": str(exc)})
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
    return avatar_response(asset)


@router.post("/avatar/{avatar_id}/fullbody/front-2d", response_model=AvatarAssetResponse)
@limiter.limit(lambda: f"{SETTINGS.companion_avatar_generate_rate_limit_per_minute}/minute")
async def post_fullbody_front_2d(
    request: Request,
    avatar_id: int,
    body: Fullbody2dFrontGenerateRequest,
    user: CurrentUser,
) -> AvatarAssetResponse:
    try:
        async with get_avatar_job_lock(user.id):
            asset = await generate_fullbody_front_2d(
                user_id=user.id,
                avatar_id=avatar_id,
                feedback=body.feedback,
                mode=body.mode,
            )
    except AvatarNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": "找不到对应的形象", "reason": str(exc)})
    except ImageSealedError as exc:
        raise HTTPException(status_code=409, detail={"error": "形象已确认锁定，无法重新生成", "reason": str(exc)})
    except SeedPromptMissingError as exc:
        raise HTTPException(status_code=400, detail={"error": "头像缺失提示词缓存，请重新生成头像", "reason": str(exc)})
    except AvatarSourceUnreadableError as exc:
        raise HTTPException(status_code=409, detail={"error": str(exc)})
    except FullbodyGenerationError as exc:
        err_detail = getattr(exc, "internal", str(exc))
        logger.warning("fullbody front-2d generation failed", extra={"user_id": user.id, "error": err_detail})
        raise HTTPException(status_code=502, detail={"error": str(exc), "reason": str(exc)})
    except AvatarGenerationError as exc:
        logger.warning("fullbody front-2d guard rejected", extra={"user_id": user.id, "error": exc.internal})
        raise HTTPException(status_code=400, detail={"error": str(exc)})
    except MissingLlmConfigError as exc:
        logger.warning("post_fullbody_front_2d missing config", extra={"user_id": user.id, "error": str(exc)})
        raise HTTPException(
            status_code=502,
            detail={"error": "LLM provider 未配置，请先在设置中配置 chat provider", "reason": str(exc)},
        )
    return avatar_response(asset)


@router.post("/avatar/{avatar_id}/fullbody/front-3d", response_model=AvatarAssetResponse)
@limiter.limit(lambda: f"{SETTINGS.companion_avatar_generate_rate_limit_per_minute}/minute")
async def post_fullbody_front_3d(
    request: Request,
    avatar_id: int,
    body: Fullbody3dSeedGenerateRequest,
    user: CurrentUser,
) -> AvatarAssetResponse:
    try:
        async with get_avatar_job_lock(user.id):
            asset = await generate_fullbody_front_3d(
                user_id=user.id,
                avatar_id=avatar_id,
                feedback=body.feedback,
                mode=body.mode,
            )
    except AvatarNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": "找不到对应的形象", "reason": str(exc)})
    except FrontSeedMissingError as exc:
        raise HTTPException(status_code=400, detail={"error": "请先确认 2D 正面全身图", "reason": str(exc)})
    except AvatarSourceUnreadableError as exc:
        raise HTTPException(status_code=409, detail={"error": str(exc)})
    except FullbodyGenerationError as exc:
        err_detail = getattr(exc, "internal", str(exc))
        logger.warning("fullbody front-3d generation failed", extra={"user_id": user.id, "error": err_detail})
        raise HTTPException(status_code=502, detail={"error": str(exc), "reason": str(exc)})
    except AvatarGenerationError as exc:
        logger.warning("fullbody front-3d guard rejected", extra={"user_id": user.id, "error": exc.internal})
        raise HTTPException(status_code=400, detail={"error": str(exc)})
    except MissingLlmConfigError as exc:
        logger.warning("post_fullbody_front_3d missing config", extra={"user_id": user.id, "error": str(exc)})
        raise HTTPException(
            status_code=502,
            detail={"error": "LLM provider 未配置，请先在设置中配置 chat provider", "reason": str(exc)},
        )
    return avatar_response(asset)


@router.post("/avatar/{avatar_id}/fullbody/back", response_model=AvatarAssetResponse)
@limiter.limit(lambda: f"{SETTINGS.companion_avatar_generate_rate_limit_per_minute}/minute")
async def post_fullbody_back(
    request: Request,
    avatar_id: int,
    body: Fullbody3dSeedGenerateRequest,
    user: CurrentUser,
) -> AvatarAssetResponse:
    try:
        async with get_avatar_job_lock(user.id):
            asset = await generate_fullbody_back(
                user_id=user.id,
                avatar_id=avatar_id,
                feedback=body.feedback,
                mode=body.mode,
            )
    except AvatarNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": "找不到对应的形象", "reason": str(exc)})
    except FrontSeedMissingError as exc:
        raise HTTPException(status_code=400, detail={"error": "请先生成正面全身图", "reason": str(exc)})
    except AvatarSourceUnreadableError as exc:
        raise HTTPException(status_code=409, detail={"error": str(exc)})
    except FullbodyGenerationError as exc:
        err_detail = getattr(exc, "internal", str(exc))
        logger.warning("fullbody back generation failed", extra={"user_id": user.id, "error": err_detail})
        raise HTTPException(status_code=502, detail={"error": str(exc), "reason": str(exc)})
    except AvatarGenerationError as exc:
        logger.warning("fullbody back guard rejected", extra={"user_id": user.id, "error": exc.internal})
        raise HTTPException(status_code=400, detail={"error": str(exc)})
    except MissingLlmConfigError as exc:
        logger.warning("post_fullbody_back missing config", extra={"user_id": user.id, "error": str(exc)})
        raise HTTPException(
            status_code=502,
            detail={"error": "LLM provider 未配置，请先在设置中配置 chat provider", "reason": str(exc)},
        )
    return avatar_response(asset)


@router.post("/avatar/{avatar_id}/fullbody/confirm-front", response_model=AvatarAssetResponse)
@limiter.limit(lambda: f"{SETTINGS.companion_avatar_generate_rate_limit_per_minute}/minute")
async def post_fullbody_confirm_front(
    request: Request,
    avatar_id: int,
    body: FullbodyConfirmFrontRequest,
    user: CurrentUser,
    db: DbSession,
) -> AvatarAssetResponse:
    try:
        async with get_avatar_job_lock(user.id):
            asset = await confirm_fullbody_front(
                db,
                user.id,
                avatar_id=avatar_id,
                front_url=body.front_url,
            )
    except AvatarNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": "找不到对应的形象", "reason": str(exc)})
    except ImageSealedError as exc:
        raise HTTPException(status_code=409, detail={"error": "形象已确认锁定，无法重新生成", "reason": str(exc)})
    except FrontSeedMissingError as exc:
        raise HTTPException(status_code=400, detail={"error": "请先生成正面全身图", "reason": str(exc)})
    except AvatarSourceUnreadableError as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": "全身立绘草稿已过期，请重新生成正面全身图", "reason": str(exc)},
        )
    try:
        await schedule_initial_room(user.id)
    except Exception:
        logger.warning("initial room scheduling failed", extra={"user_id": user.id}, exc_info=True)
    return avatar_response(asset)


def _fullbody_self_source_http_error(exc: AvatarGenerationError) -> HTTPException:
    """自备图提示词/采纳端点共用的错误映射：语义与对应生成端点一致。"""
    if isinstance(exc, AvatarNotFoundError):
        return HTTPException(status_code=404, detail={"error": "找不到对应的形象", "reason": str(exc)})
    if isinstance(exc, ImageSealedError):
        return HTTPException(status_code=409, detail={"error": "形象已确认锁定，无法重新生成", "reason": str(exc)})
    if isinstance(exc, AvatarSourceUnreadableError):
        # 与 AI 生成端点一致：种子/头像文件不可读是需用户先修复源资产的状态冲突，不是普通请求错误。
        return HTTPException(status_code=409, detail={"error": str(exc)})
    if isinstance(exc, SeedPromptMissingError):
        return HTTPException(
            status_code=400,
            detail={"error": "头像缺失提示词缓存，请重新生成头像", "reason": str(exc)},
        )
    if isinstance(exc, FrontSeedMissingError):
        return HTTPException(status_code=400, detail={"error": "请先生成或确认正面全身图", "reason": str(exc)})
    return HTTPException(status_code=400, detail={"error": str(exc)})


@router.post("/avatar/{avatar_id}/fullbody/{kind}/prompt", response_model=ImagePromptResponse)
@limiter.limit(lambda: f"{SETTINGS.companion_avatar_generate_rate_limit_per_minute}/minute")
async def post_fullbody_prompt(
    request: Request,
    avatar_id: int,
    kind: FullbodySeedKind,
    user: CurrentUser,
    body: FullbodyPromptRequest = Body(default_factory=FullbodyPromptRequest),
) -> ImagePromptResponse:
    """自备图提示词：按种子参考图锚定组装，种子缺失与 AI 路径同样失败，不做生图。"""
    try:
        prompt = await prepare_fullbody_prompt(
            user_id=user.id,
            avatar_id=avatar_id,
            kind=kind,
            feedback=body.feedback,
        )
    except AvatarGenerationError as exc:
        raise _fullbody_self_source_http_error(exc)
    except MissingLlmConfigError as exc:
        logger.warning("fullbody prompt missing config", extra={"user_id": user.id, "error": str(exc)})
        raise HTTPException(
            status_code=502,
            detail={"error": "生成服务未配置，请先在设置中配置供应商", "reason": str(exc)},
        )
    return ImagePromptResponse(prompt=prompt)


@router.post(
    "/avatar/{avatar_id}/fullbody/{kind}/adopt",
    response_model=AvatarAssetResponse,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit(lambda: f"{SETTINGS.companion_avatar_generate_rate_limit_per_minute}/minute")
async def post_fullbody_adopt(
    request: Request,
    avatar_id: int,
    kind: FullbodySeedKind,
    body: FullbodyAdoptRequest,
    user: CurrentUser,
) -> AvatarAssetResponse:
    """自备图采纳：用户外部生成的图像按对应种子生成成功的语义落库。"""
    raw, content_type = _decode_upload_image(body.image, body.content_type)
    if not raw:
        raise HTTPException(status_code=400, detail="Invalid image data")
    try:
        asset = await adopt_fullbody_seed(
            user_id=user.id,
            avatar_id=avatar_id,
            kind=kind,
            data=raw,
            content_type=content_type or "image/png",
        )
    except AvatarGenerationError as exc:
        raise _fullbody_self_source_http_error(exc)
    except MissingLlmConfigError as exc:
        logger.warning("fullbody adopt missing config", extra={"user_id": user.id, "error": str(exc)})
        raise HTTPException(
            status_code=502,
            detail={"error": "生成服务未配置，请先在设置中配置供应商", "reason": str(exc)},
        )
    return avatar_response(asset)


@router.get("/model", response_model=Companion3DModelResponse | None)
async def get_model(user: CurrentUser, db: DbSession) -> Companion3DModelResponse | None:
    model = await get_active_model(db, user.id)
    if model is None:
        return None
    return model_response(model)


@router.post("/model", response_model=Companion3DModelResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit(lambda: f"{SETTINGS.companion_model_generate_rate_limit_per_minute}/minute")
async def post_model(
    request: Request,  # required by @limiter.limit
    user: CurrentUser,
    db: DbSession,
    body: ModelGenerateRequest = Body(default_factory=ModelGenerateRequest),
) -> Companion3DModelResponse:
    try:
        model = await generate_companion_model(
            db,
            user_id=user.id,
            species_override=body.species_override,
            provider_override=body.provider,
            force=body.force,
        )
    except ModelGenerationInProgressError as exc:
        logger.info("post_model already in progress", extra={"user_id": user.id, "error": str(exc)})
        raise HTTPException(status_code=409, detail={"error": str(exc)})
    except ModelProviderNotConfiguredError as exc:
        logger.warning("post_model provider not configured", extra={"user_id": user.id, "error": str(exc)})
        raise HTTPException(status_code=400, detail={"error": str(exc)})
    except ModelGenerationError as exc:
        logger.warning("post_model generation error", extra={"user_id": user.id, "error": str(exc)})
        raise HTTPException(status_code=502, detail={"error": str(exc)})
    return model_response(model)


@router.get("/2d", response_model=Companion2DModelResponse | None)
async def get_mesh2d(user: CurrentUser, db: DbSession) -> Companion2DModelResponse | None:
    return await get_active_mesh2d_response(db, user.id)


@router.post("/2d", response_model=Companion2DModelResponse, status_code=status.HTTP_202_ACCEPTED)
async def post_mesh2d(user: CurrentUser, db: DbSession) -> Companion2DModelResponse:
    try:
        persona = await get_or_create_persona(db, user.id)
        priority = "low" if persona.render_mode == "3d" else "high"
        model = await generate_mesh2d_model(db, user_id=user.id, priority=priority)
    except Mesh2DNotReadyError as exc:
        logger.warning("2d generation failed to start", extra={"user_id": user.id, "error": str(exc)})
        raise HTTPException(status_code=409, detail={"error": str(exc), "reason": "startup_failed"})

    response = await get_active_mesh2d_response(db, user.id)
    return response or Companion2DModelResponse(id=model.id, status=model.status)


@router.post("/render-mode", response_model=PersonaResponse)
async def post_render_mode(body: RenderModeRequest, user: CurrentUser, db: DbSession) -> PersonaResponse:
    persona = await set_render_mode(db, user_id=user.id, render_mode=body.render_mode)

    if body.render_mode == "3d":
        try:
            await generate_companion_model(db, user_id=user.id, force=False)
        except ModelGenerationError as exc:
            logger.info("render_mode 3D dispatch skipped", extra={"user_id": user.id, "error": str(exc)})

    return PersonaResponse(
        definition_json=persona.definition_json or "{}",
        is_complete=persona.is_complete,
        personality_tags=[],
        render_mode=persona.render_mode or "2d",
        current_mood=persona.current_mood,
    )


def _outfit_http_error(exc: OutfitError) -> HTTPException:
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
    except OutfitError as exc:
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
    except OutfitError as exc:
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
    except OutfitError as exc:
        raise _outfit_http_error(exc)
    except AvatarGenerationError as exc:
        # AvatarGenerationError 的 str 按契约是公开文案（含编辑能力缺失等可行动指引），透传不替换。
        logger.warning(
            "outfit draft regenerate failed",
            extra={"user_id": user.id, "outfit_id": outfit_id, "error": getattr(exc, "internal", str(exc))},
        )
        raise HTTPException(status_code=502, detail={"error": str(exc), "reason": "generation_failed"})
    return outfit_response(outfit)


@router.post("/outfits/{outfit_id}/poses/{side}/regenerate", response_model=OutfitResponse)
async def post_outfit_pose_regenerate(
    outfit_id: int,
    side: Literal["left", "right"],
    user: CurrentUser,
    db: DbSession,
) -> OutfitResponse:
    """单侧重生成一侧扶边姿态：校验后就地入队（202 语义），完成与失败经 WS 事件驱动刷新。"""
    try:
        outfit = await regenerate_outfit_pose(db, user.id, outfit_id, side)
    except OutfitError as exc:
        raise _outfit_http_error(exc)
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
    except OutfitError as exc:
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


@router.post("/outfits/{outfit_id}/poses/{side}/prompt", response_model=ImagePromptResponse)
async def post_outfit_pose_prompt(
    outfit_id: int,
    side: Literal["left", "right"],
    user: CurrentUser,
    db: DbSession,
) -> ImagePromptResponse:
    """自备图提示词（单侧扶边姿态）：身份与穿着由当前外观正面立绘锚定；立绘不可读时 409。"""
    try:
        prompt = await prepare_pose_prompt(db, user.id, outfit_id, side)
    except OutfitError as exc:
        raise _outfit_http_error(exc)
    return ImagePromptResponse(prompt=prompt)


@router.post(
    "/outfits/{outfit_id}/poses/{side}/adopt",
    response_model=OutfitResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def post_outfit_pose_adopt(
    outfit_id: int,
    side: Literal["left", "right"],
    body: ImageAdoptRequest,
    user: CurrentUser,
    db: DbSession,
) -> OutfitResponse:
    """自备图采纳（单侧姿态）：校验后就地入队既有单侧管线（跳过主图生图），完成经 WS 事件驱动刷新。"""
    raw, content_type = _decode_upload_image(body.image, body.content_type)
    if not raw:
        raise HTTPException(status_code=400, detail="Invalid image data")
    try:
        outfit = await adopt_outfit_pose(db, user.id, outfit_id, side, data=raw)
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
    """确认入柜；姿态图字段契约见 PIPELINE §1.1.2。与同模块其他 POST 一致：
    以可缺省的 Pydantic 模型收 body，空对象不触发 422。"""
    user_poses: dict[Literal["left", "right"], bytes] | None = None
    poses: dict[Literal["left", "right"], bytes] = {}
    for side, image_b64, content_type in (
        ("left", body.pose_left, body.pose_left_content_type),
        ("right", body.pose_right, body.pose_right_content_type),
    ):
        if not image_b64:
            continue
        raw, _ = _decode_upload_image(image_b64, content_type)
        if not raw:
            raise HTTPException(status_code=400, detail={"error": f"{'左' if side == 'left' else '右'}姿态图数据无效"})
        if side == "left":
            poses["left"] = raw
        else:
            poses["right"] = raw
    user_poses = poses or None

    try:
        outfit = await confirm_outfit(db, user.id, outfit_id, user_poses=user_poses)
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


@public_router.get("/model/file/{user_id}/{filename:path}")
async def serve_model_file(
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
    result = resolve_companion_model_path(user_id, filename)
    if result is None:
        raise HTTPException(status_code=404, detail="Model not found")
    path, content_type = result
    return await serve_ranged_file(request, path, content_type)
