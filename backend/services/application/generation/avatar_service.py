import asyncio
import base64
import contextlib
import hashlib
import json
import secrets
from pathlib import Path

from components import (
    REMOTE_ASSET_DOWNLOAD_MAX_BYTES,
    SESSION_LOCAL,
    SETTINGS,
    download_capped,
    get_file_path,
    get_logger,
    parse_llm_json,
    safe_json_loads,
    save_file,
)
from modules.companion import (
    AvatarAsset,
    BodyFeatures,
    CharacterCardSnapshot,
    CharacterFeatures,
    CharacterOverrides,
    CompanionOutfit,
    FullbodyCandidate,
    FullbodyCandidateResponse,
    ImageReviseMode,
    Persona,
    PortraitFeatures,
)
from modules.ws import emit_ws_event
from prompts.generation import (
    CHARACTER_CARD_EXTRACTION,
    EDIT_PRESERVE_AVATAR,
    EDIT_PRESERVE_FULLBODY,
    MODERATION_SANITIZATION_PROMPT,
    PORTRAIT_IDENTITY_TEMPLATE,
)
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from services.domains.companion import (
    character_snapshot_is_current,
    emit_character_card_updated,
    get_character_card,
    get_or_create_persona,
    load_persona_definition,
    register_character_card,
    require_character_snapshot,
)
from services.infrastructure.assets import build_data_uri, build_signed_avatar_url, resolve_companion_asset_path
from services.infrastructure.llm import (
    SIZE_TO_ASPECT,
    build_avatar_prompt_from_appearance,
    build_avatar_reference_prompt,
    build_image_edit_prompt,
    chat,
    describe_character_form,
    enhance_avatar_prompt,
    is_content_policy_error_message,
    vision_chat,
)

from .appearance_preparation import (
    AppearanceParts,
    AppearancePreparationError,
    AppearanceSourceChangedError,
    prepare_appearance_parts,
)
from .fullbody_reference_prompt import build_fullbody_reference_prompt
from .image_generation import ImageGenerationError, generate_images

logger = get_logger(__name__)

_DEFAULT_STYLE: str = "portrait"
_AVATAR_SIZE: str = "1024x1024"
# 全身种子竖版画幅（DESIGN §5.4）：9:16，作为参考立绘与视频链身份的输入。
_FULLBODY_SIZE: str = "1024x1792"
_FULLBODY_ASPECT: str = SIZE_TO_ASPECT[_FULLBODY_SIZE]
_AVATAR_QUALITY: str = "standard"
_AVATAR_IMAGE_FIELDS: tuple[str, ...] = (
    "asset_url",
    "seed_fullbody_url",
)
_UPLOAD_EXTS: dict[str, str] = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}
ALLOWED_AVATAR_UPLOAD_MIME_TYPES: frozenset[str] = frozenset(_UPLOAD_EXTS)

# 按用户加锁，避免 REST 头像路由与 WS RPC 并发再生成/选择时抢同一行
AVATAR_JOB_LOCKS: dict[int, asyncio.Lock] = {}


async def _sanitize_prompt_for_moderation(user_id: int, prompt: str) -> str:
    """合规改写被审核拒绝的提示词，失败时返回原文。"""
    try:
        raw = await chat(None, user_id, MODERATION_SANITIZATION_PROMPT, prompt)
        payload = parse_llm_json(raw)
        sanitized = payload.get("prompt") if isinstance(payload, dict) and set(payload) == {"prompt"} else None
        return sanitized.strip() if isinstance(sanitized, str) and sanitized.strip() else prompt
    except Exception:
        # 失败回退到原文属设计意图：内容审核改写是可选优化；保留 exc_info 供排查 LLM/网络层问题。
        logger.warning(
            "prompt sanitization LLM call failed; falling back to original prompt",
            extra={"user_id": user_id},
            exc_info=True,
        )
        return prompt


async def _generate_one_portrait_with_moderation_retry(
    prompt: str,
    user_id: int,
    *,
    reference_image: str | None = None,
    secondary_reference_image: str | None = None,
    size: str = _AVATAR_SIZE,
    persist: bool = True,
    image_edit: bool = False,
) -> tuple[str, str, str, str]:
    """生成一张立绘；命中内容审核时用改写后的提示词重试一次。image_edit=True 时参考图是编辑底图，供应商链按图像编辑能力过滤。"""
    try:
        return await _generate_one_portrait(
            prompt,
            user_id,
            reference_image=reference_image,
            secondary_reference_image=secondary_reference_image,
            size=size,
            persist=persist,
            image_edit=image_edit,
        )
    except AvatarGenerationError as first_exc:
        if not is_content_policy_error_message(first_exc.internal):
            raise
        logger.info("avatar gen blocked by moderation, sanitizing prompt", extra={"user_id": user_id})
        sanitized = await _sanitize_prompt_for_moderation(user_id, prompt)
        if sanitized == prompt:
            raise  # 改写后内容无变化，再调一次 API 也是白费
        try:
            return await _generate_one_portrait(
                sanitized,
                user_id,
                reference_image=reference_image,
                secondary_reference_image=secondary_reference_image,
                size=size,
                persist=persist,
                image_edit=image_edit,
            )
        except AvatarGenerationError as second_exc:
            raise AvatarGenerationError(
                "生成请求被内容审核拦截，请调整描述后重试",
                internal=f"original: {first_exc.internal}; retry: {second_exc.internal}",
            ) from second_exc


class AvatarGenerationError(RuntimeError):
    """形象生成失败；str(exc) 恒为可展示的公开文案，上游原始错误只放在 internal 供日志与流程判断。"""

    def __init__(self, public: str, internal: str = "") -> None:
        super().__init__(public)
        self.internal = internal or public


class AvatarAppearancePreparationError(AvatarGenerationError):
    """头像或全身描述拆分失败；本次图像请求未提交。"""


class AvatarAppearanceChangedError(AvatarGenerationError):
    """拆分等待期间用户修改了外貌资料。"""


class AvatarNotFoundError(AvatarGenerationError):
    """目标头像行不存在或不属于调用者。"""


class FullbodyGenerationError(AvatarGenerationError):
    """全身图生成失败。"""


async def _prepare_appearance_parts(user_id: int, persona: Persona) -> AppearanceParts | None:
    """只有原始外貌描述非空时才调用拆分模型，并将状态错误映射到形象业务错误。"""
    appearance = str(load_persona_definition(persona).get("appearance") or "").strip()
    try:
        return await prepare_appearance_parts(user_id, expected_appearance=appearance)
    except AppearanceSourceChangedError as exc:
        raise AvatarAppearanceChangedError(str(exc), internal=str(exc)) from exc
    except AppearancePreparationError as exc:
        raise AvatarAppearancePreparationError(str(exc), internal=str(exc)) from exc


def _avatar_creative_prompt(
    persona: Persona,
    *,
    appearance: str,
    feedback: str | None = None,
    reference_image: bool = False,
    presentation_image: bool = False,
) -> str:
    definition = load_persona_definition(persona)
    personality = str(definition.get("personality") or "").strip()
    if reference_image:
        return build_avatar_reference_prompt(
            appearance_description=appearance,
            personality=personality,
            feedback=feedback,
            has_presentation_reference=presentation_image,
        )
    return build_avatar_prompt_from_appearance(
        biological_type=str(definition.get("biological_type") or "").strip(),
        gender=str(definition.get("gender") or "").strip(),
        appearance=appearance,
        personality=personality,
        feedback=feedback,
    )


def fullbody_candidate_response(candidate: FullbodyCandidate) -> FullbodyCandidateResponse:
    return FullbodyCandidateResponse(
        id=candidate.id,
        avatar_id=candidate.avatar_id,
        image_url=re_sign_bare_path(candidate.image_url) or candidate.image_url,
        status=candidate.status,
        error=candidate.error,
    )


def _portrait_identity(identity: CharacterCardSnapshot | None) -> str:
    if identity is None:
        return ""
    portrait = PortraitFeatures.model_validate(
        {key: getattr(identity.features, key) for key in PortraitFeatures.model_fields},
    )
    return PORTRAIT_IDENTITY_TEMPLATE.format(features=json.dumps(portrait.model_dump(), ensure_ascii=False))


async def latest_fullbody_candidate(user_id: int, avatar_id: int) -> FullbodyCandidateResponse | None:
    async with SESSION_LOCAL() as db:
        row = await db.scalar(
            select(FullbodyCandidate)
            .where(FullbodyCandidate.user_id == user_id, FullbodyCandidate.avatar_id == avatar_id)
            .order_by(FullbodyCandidate.id.desc())
            .limit(1),
        )
        if row is None or row.status in ("accepted", "rejected"):
            return None
        avatar = await db.get(AvatarAsset, avatar_id)
        card = await get_character_card(db, user_id)
        if (
            avatar is None
            or not avatar.active
            or card is None
            or row.base_fullbody_url != avatar.seed_fullbody_url
            or row.base_revision != card.revision
        ):
            return None
        return fullbody_candidate_response(row)


async def _analyze_fullbody_candidate(user_id: int, candidate_id: int) -> FullbodyCandidateResponse:
    async with SESSION_LOCAL() as db:
        row = await db.scalar(
            select(FullbodyCandidate).where(FullbodyCandidate.id == candidate_id, FullbodyCandidate.user_id == user_id),
        )
        if row is None or row.status not in ("pending", "failed"):
            raise AvatarNotFoundError("全身候选图不存在或已采纳")
        path = row.image_url
    features: BodyFeatures | None = None
    source_hash = ""
    try:
        uri = await asyncio.to_thread(load_avatar_bytes_as_data_uri, path)
        if not uri:
            raise ValueError("candidate image is unreadable")
        source_hash = hashlib.sha256(base64.b64decode(uri.split(",", 1)[1], validate=True)).hexdigest()
        schema = BodyFeatures.model_json_schema()
        schema["required"] = list(BodyFeatures.model_fields)
        async with asyncio.timeout(180):
            raw = await vision_chat(
                user_id,
                CHARACTER_CARD_EXTRACTION,
                json.dumps({"source": "body", "schema": schema}, ensure_ascii=False),
                reference_images=(uri,),
            )
        payload = parse_llm_json(raw)
        if not isinstance(payload, dict) or set(payload) != set(BodyFeatures.model_fields):
            raise ValueError("incomplete body extraction")
        features = BodyFeatures.model_validate(payload)
    except Exception:
        logger.warning("fullbody candidate analysis failed", extra={"candidate_id": candidate_id}, exc_info=True)
    async with SESSION_LOCAL() as db:
        row = await db.scalar(
            select(FullbodyCandidate)
            .where(FullbodyCandidate.id == candidate_id, FullbodyCandidate.user_id == user_id)
            .with_for_update(),
        )
        if row is None or row.status not in ("pending", "failed") or row.image_url != path:
            raise AvatarGenerationError("全身候选图已变化，请刷新")
        row.status = "ready" if features is not None else "failed"
        row.error = None if features is not None else "身体特征分析失败，请重试分析"
        if features is not None:
            row.body_features_json = features.model_dump_json()
            row.body_source_hash = source_hash
        await db.commit()
        return fullbody_candidate_response(row)


async def retry_fullbody_candidate_analysis(
    user_id: int,
    avatar_id: int,
    candidate_id: int,
) -> FullbodyCandidateResponse:
    async with SESSION_LOCAL() as db:
        row = await db.scalar(
            select(FullbodyCandidate).where(
                FullbodyCandidate.id == candidate_id,
                FullbodyCandidate.user_id == user_id,
                FullbodyCandidate.avatar_id == avatar_id,
            ),
        )
        if row is None:
            raise AvatarNotFoundError("全身候选图不存在")
    return await _analyze_fullbody_candidate(user_id, candidate_id)


async def _save_fullbody_candidate(
    user_id: int,
    avatar_id: int,
    image_url: str,
    identity: CharacterCardSnapshot,
    base_fullbody_url: str,
) -> FullbodyCandidateResponse:
    replaced_paths: list[str] = []
    try:
        async with SESSION_LOCAL() as db:
            avatar = await db.get(AvatarAsset, avatar_id)
            if (
                avatar is None
                or avatar.user_id != user_id
                or not avatar.active
                or avatar.seed_fullbody_url != base_fullbody_url
                or not await character_snapshot_is_current(db, user_id, identity)
            ):
                raise AvatarGenerationError("形象或角色卡已更新，请重新生成全身图")
            previous = (
                await db.scalars(
                    select(FullbodyCandidate).where(
                        FullbodyCandidate.user_id == user_id,
                        FullbodyCandidate.avatar_id == avatar_id,
                        FullbodyCandidate.status.in_(("pending", "ready", "failed")),
                    ),
                )
            ).all()
            for older in previous:
                older.status = "rejected"
                replaced_paths.append(older.image_url)
            row = FullbodyCandidate(
                user_id=user_id,
                avatar_id=avatar_id,
                base_fullbody_url=base_fullbody_url,
                base_revision=identity.revision,
                image_url=image_url,
                status="pending",
            )
            db.add(row)
            await db.commit()
            candidate_id = row.id
    except BaseException:
        delete_portrait_file(image_url)
        raise
    for path in replaced_paths:
        delete_portrait_file(path)
    return await _analyze_fullbody_candidate(user_id, candidate_id)


async def accept_fullbody_candidate(user_id: int, avatar_id: int, candidate_id: int) -> AvatarAsset:
    async with get_avatar_job_lock(user_id), SESSION_LOCAL() as db:
        row = await db.scalar(
            select(FullbodyCandidate)
            .where(
                FullbodyCandidate.id == candidate_id,
                FullbodyCandidate.user_id == user_id,
                FullbodyCandidate.avatar_id == avatar_id,
            )
            .with_for_update(),
        )
        avatar = await db.get(AvatarAsset, avatar_id, with_for_update=True)
        card = await get_character_card(db, user_id, lock=True)
        if (
            row is None
            or avatar is None
            or card is None
            or avatar.user_id != user_id
            or not avatar.active
            or not avatar.is_fullbody_confirmed
        ):
            raise AvatarNotFoundError("全身候选图或当前角色不存在")
        if row.status != "ready":
            raise AvatarGenerationError("请先完成候选图的身体特征分析")
        if (
            card.status != "ready"
            or card.revision != row.base_revision
            or avatar.seed_fullbody_url != row.base_fullbody_url
        ):
            raise AvatarGenerationError("角色资料已更新，请重新生成全身图")
        if not await asyncio.to_thread(load_avatar_bytes_as_data_uri, row.image_url):
            raise AvatarSourceUnreadableError("全身候选图已无法读取，请重新生成")
        current = CharacterFeatures.model_validate_json(card.automatic_json)
        portrait = PortraitFeatures.model_validate(
            {key: getattr(current, key) for key in PortraitFeatures.model_fields},
        )
        body = BodyFeatures.model_validate_json(row.body_features_json)
        overrides = CharacterOverrides.model_validate_json(card.overrides_json)
        card.automatic_json = CharacterFeatures(**portrait.model_dump(), **body.model_dump()).model_dump_json()
        card.overrides_json = CharacterOverrides(
            **{key: getattr(overrides, key) for key in PortraitFeatures.model_fields},
        ).model_dump_json(exclude_none=True)
        card.body_result_json = body.model_dump_json()
        card.body_source_path = row.image_url
        card.body_source_hash = row.body_source_hash
        card.body_pending_hash = row.body_source_hash
        card.body_status = "ready"
        card.revision += 1
        card.error = None
        avatar.seed_fullbody_url = row.image_url
        row.status = "accepted"
        emit_character_card_updated(db, card)
        await db.commit()
        db.expunge(avatar)
        _re_sign_avatar_url(avatar)
        return avatar


class AvatarSourceUnreadableError(AvatarGenerationError):
    """头像文件已无法从磁盘读取，需用户重新生成后再重试。"""


class ImageSealedError(AvatarGenerationError):
    """形象已确认锁定：头像重生关闭，全身种子仍可在保持身份的前提下重绘。"""


async def raise_if_image_sealed(db: AsyncSession | None, user_id: int, persona: Persona) -> None:
    """以激活头像的全身确认标志锁定身份，客户端隐藏入口不能代替服务端守卫。"""
    if not (persona.is_complete and persona.is_portrait_confirmed):
        return

    async def _sealed(session: AsyncSession) -> bool:
        asset = (
            await session.execute(
                select(AvatarAsset).where(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)),
            )
        ).scalar_one_or_none()
        return bool(asset and asset.is_fullbody_confirmed)

    if db is not None:
        sealed = await _sealed(db)
    else:
        async with SESSION_LOCAL() as probe_db:
            sealed = await _sealed(probe_db)

    if sealed:
        raise ImageSealedError("形象已确认锁定，无法重新生成")


def get_avatar_job_lock(user_id: int) -> asyncio.Lock:
    """惰性创建并返回用户级锁；条目不回收（锁很小且 user_id 空间有限）。"""
    return AVATAR_JOB_LOCKS.setdefault(user_id, asyncio.Lock())


async def _persist_portrait_bytes(data: bytes, content_type: str) -> tuple[str, str, str]:
    """把立绘字节原样写入 companion-avatars/ 并返回 (裸存储路径, file_id, ext)。"""
    src_content_type = content_type.split(";", maxsplit=1)[0].strip().lower()
    final_ext = _UPLOAD_EXTS.get(src_content_type, "jpg")
    file_id = secrets.token_urlsafe(16)
    avatars_dir = Path(SETTINGS.data_dir) / "companion-avatars"

    def _write() -> None:
        avatars_dir.mkdir(parents=True, exist_ok=True)
        with open(avatars_dir / f"{file_id}.{final_ext}", "wb") as f:
            f.write(data)

    # 取消时等待线程收敛，删除尚未交给调用方的文件。
    task = asyncio.create_task(asyncio.to_thread(_write))
    try:
        await asyncio.shield(task)
    except BaseException:
        await asyncio.gather(task, return_exceptions=True)
        with contextlib.suppress(OSError):
            (avatars_dir / f"{file_id}.{final_ext}").unlink(missing_ok=True)
        raise

    # 行内存裸路径而非签名 URL，避免过期；读取时再签名
    return _avatar_storage_path(file_id, final_ext), file_id, final_ext


def _avatar_storage_path(file_id: str, ext: str) -> str:
    """返回立绘的规范裸存储路径 companion-avatars/<file_id>.<ext>。"""
    return f"companion-avatars/{file_id}.{ext}"


async def _persist_portrait_or_draft(
    data: bytes,
    user_id: int,
    content_type: str,
    *,
    persist: bool,
) -> tuple[str, str, str]:
    """按确认语义落盘立绘字节：persist=True 写 companion-avatars/ 永久路径，False 写 temp-media/ 草稿（TTL 24h）。
    返回 (存储裸路径, file_id, ext)；不需要 file_id 的调用方忽略后两项。"""
    if persist:
        return await _persist_portrait_bytes(data, content_type)
    src_content_type = content_type.split(";", maxsplit=1)[0].strip().lower()
    final_ext = _UPLOAD_EXTS.get(src_content_type, "jpg")
    file_id, _public_url = await asyncio.to_thread(
        save_file,
        data,
        f"user:{user_id}",
        src_content_type,
        final_ext,
        meta_marker=f"preview:{user_id}",
    )
    return f"temp-media/{file_id}", file_id, final_ext


def _temp_media_public_url(bare_path: str) -> str:
    """temp-media 路径不做 HMAC 签名，由 /api/media/files/{file_id} 免鉴权提供。"""
    if bare_path.startswith("temp-media/"):
        file_id = bare_path.split("/", 1)[1]
        return f"/api/media/files/{file_id}"
    return bare_path


async def _download_to_bytes(url: str) -> tuple[bytes, str] | None:
    """把生成结果 URL 解析为 (bytes, content_type)，不可达时返回 None；远端请求禁用重定向并走出网安全校验。"""
    if "/api/media/files/" in url:
        fid = url.rsplit("/", 1)[-1].split("?", maxsplit=1)[0]
        res = get_file_path(fid)
        if res:
            path, content_type = res
            return await asyncio.to_thread(Path(path).read_bytes), content_type
    try:
        content = await download_capped(url, max_bytes=REMOTE_ASSET_DOWNLOAD_MAX_BYTES, timeout=360.0)
        ct = "image/jpeg"
        if content.startswith(b"\x89PNG"):
            ct = "image/png"
        elif content.startswith(b"RIFF") and b"WEBP" in content[:12]:
            ct = "image/webp"
        return content, ct
    except Exception:
        return None


def _extract_temp_file_id(source_url: str) -> str | None:
    marker = "/api/media/files/"
    idx = source_url.find(marker)
    if idx < 0:
        return None
    return source_url[idx + len(marker) :].split("?", maxsplit=1)[0].split("/", maxsplit=1)[0] or None


async def _generate_one_portrait(
    prompt: str,
    user_id: int,
    *,
    reference_image: str | None = None,
    secondary_reference_image: str | None = None,
    size: str = _AVATAR_SIZE,
    persist: bool = True,
    image_edit: bool = False,
) -> tuple[str, str, str, str]:
    """persist=False 时图片留在 temp-media/（引导流程），True 时落盘到 companion-avatars/。image_edit=True 时走图像编辑供应商链（编辑底图经 reference_image 传入，不接受 secondary）。"""
    try:
        urls = await generate_images(
            prompt,
            size=size,
            n=1,
            user_id=user_id,
            reference_image=reference_image,
            secondary_reference_image=secondary_reference_image,
            image_edit=image_edit,
        )
    except ImageGenerationError as exc:
        logger.warning("portrait image generation failed", extra={"user_id": user_id, "error": exc.internal})
        # ImageGenerationError 的 str 按契约是公开文案（含编辑能力缺失等可行动指引），透传不替换。
        raise AvatarGenerationError(str(exc), internal=exc.internal) from exc
    source_url = urls[0]

    if not persist:
        temp_file_id = _extract_temp_file_id(source_url)
        if temp_file_id:
            return f"temp-media/{temp_file_id}", temp_file_id, "jpg", source_url
        persist = True

    downloaded = await _download_to_bytes(source_url)
    if downloaded is None:
        raise AvatarGenerationError("生成结果下载失败，请稍后重试")
    data, content_type = downloaded
    asset_url, file_id, final_ext = await _persist_portrait_bytes(data, content_type)
    return asset_url, file_id, final_ext, source_url


async def _write_avatar_step(
    db: AsyncSession,
    user_id: int,
    *,
    asset_url: str,
    file_id: str,
    final_ext: str,
    avatar_source_url: str,
    avatar_prompt: str,
    style: str,
    feedback: str | None = None,
    reference_image: str | None = None,
    secondary_reference_image: str | None = None,
    persist: bool = False,
) -> AvatarAsset:
    previous = (
        await db.execute(select(AvatarAsset).where(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)))
    ).scalar_one_or_none()
    await db.execute(
        update(AvatarAsset).where(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)).values(active=False),
    )
    prompt_payload: dict = {
        "avatar_prompt": avatar_prompt,
        "style": style,
        "source_url": avatar_source_url,
    }
    if feedback is not None:
        prompt_payload["feedback"] = feedback
    if reference_image is not None:
        # 审计行只留 data URI 前缀标记，不落 base64 大字段
        prompt_payload["reference_image"] = reference_image.split(",", 1)[0]
    if secondary_reference_image is not None:
        prompt_payload["secondary_reference_image"] = secondary_reference_image.split(",", 1)[0]
    asset = AvatarAsset(
        user_id=user_id,
        prompt_json=json.dumps(prompt_payload, ensure_ascii=False),
        asset_url=asset_url,
        style=style,
        seed=secrets.randbelow(2**31),
        active=True,
    )
    # 用显式 SQL 更新，确保调用方传入的 persona 是游离实例时确认标记依然会被重置
    await db.execute(
        update(Persona)
        .where(Persona.user_id == user_id)
        .values(is_portrait_confirmed=False, portrait_confirmed_at=None),
    )
    db.add(asset)
    await db.commit()
    await db.refresh(asset)

    if persist:
        asset.asset_url = build_signed_avatar_url(file_id, final_ext)
        if previous is not None:
            delete_portrait_file(previous.asset_url)
    else:
        # 引导流程：temp-media URL——转换为客户端可解析的路径
        asset.asset_url = _temp_media_public_url(asset_url)

    return asset


async def _generate_avatar_step(
    db: AsyncSession | None,
    user_id: int,
    *,
    avatar_prompt: str,
    style: str,
    feedback: str | None = None,
    reference_image: str | None = None,
    secondary_reference_image: str | None = None,
    persist: bool = False,
    image_edit: bool = False,
) -> AvatarAsset:
    """先在短会话外完成立绘生成，再用一次短写会话提交新的 active AvatarAsset 行。"""
    (asset_url, file_id, final_ext, avatar_source_url) = await _generate_one_portrait_with_moderation_retry(
        avatar_prompt,
        user_id,
        reference_image=reference_image,
        secondary_reference_image=secondary_reference_image,
        persist=persist,
        image_edit=image_edit,
    )

    if db is None:
        async with SESSION_LOCAL() as write_db:
            return await _write_avatar_step(
                write_db,
                user_id,
                asset_url=asset_url,
                file_id=file_id,
                final_ext=final_ext,
                avatar_source_url=avatar_source_url,
                avatar_prompt=avatar_prompt,
                style=style,
                feedback=feedback,
                reference_image=reference_image,
                secondary_reference_image=secondary_reference_image,
                persist=persist,
            )

    return await _write_avatar_step(
        db,
        user_id,
        asset_url=asset_url,
        file_id=file_id,
        final_ext=final_ext,
        avatar_source_url=avatar_source_url,
        avatar_prompt=avatar_prompt,
        style=style,
        feedback=feedback,
        reference_image=reference_image,
        secondary_reference_image=secondary_reference_image,
        persist=persist,
    )


def delete_portrait_file(asset_url: str | None) -> None:
    """尽力删除立绘文件，兼容签名 URL、companion-avatars/ 裸路径与 temp-media/ 草稿路径。"""
    if not asset_url:
        return

    # temp-media 草稿：需经 temp_files 元数据查出真实路径再删
    temp_marker = "temp-media/"
    temp_idx = asset_url.find(temp_marker)
    if temp_idx >= 0:
        temp_file_id = asset_url[temp_idx + len(temp_marker) :].split("?")[0]
        if "/" in temp_file_id or "\\" in temp_file_id or ".." in temp_file_id:
            return
        res = get_file_path(temp_file_id)
        if res is not None:
            with contextlib.suppress(OSError):
                res[0].unlink(missing_ok=True)
        return

    name: str | None = None

    idx = asset_url.find("/api/companion/avatar/file/")
    if idx >= 0:
        name = Path(asset_url[idx + len("/api/companion/avatar/file/") :]).name

    if name is None:
        marker = "companion-avatars/"
        idx = asset_url.find(marker)
        if idx >= 0:
            name = Path(asset_url[idx + len(marker) :]).name

    if not name:
        return
    if "/" in name or "\\" in name or ".." in name:
        return
    with contextlib.suppress(OSError):
        (Path(SETTINGS.data_dir) / "companion-avatars" / name).unlink(missing_ok=True)


async def _verified_persona(db: AsyncSession | None, user_id: int, persona: Persona | None) -> Persona:
    """所有生成入口共用的前置校验：persona 存在（缺则取/建）、onboarding 完成且形象未锁定。"""
    if persona is None:
        if db is not None:
            persona = await get_or_create_persona(db, user_id)
        else:
            async with SESSION_LOCAL() as probe_db:
                persona = await get_or_create_persona(probe_db, user_id)
    if not persona.is_complete:
        raise AvatarGenerationError("persona is incomplete; finish onboarding first")
    await raise_if_image_sealed(db, user_id, persona)
    return persona


async def generate_avatar(
    db: AsyncSession | None = None,
    user_id: int | None = None,
    persona: Persona | None = None,
) -> AvatarAsset:
    """引导流程完成后生成首张立绘。"""
    if user_id is None:
        raise ValueError("user_id is required")
    persona = await _verified_persona(db, user_id, persona)
    parts = await _prepare_appearance_parts(user_id, persona)
    if parts is not None:
        avatar_prompt = _avatar_creative_prompt(persona, appearance=parts.portrait_description)
    else:
        try:
            avatar_prompt = await enhance_avatar_prompt(db, user_id, persona)
        except (ValidationError, RuntimeError) as exc:
            raise AvatarGenerationError("prompt enhancement failed", internal=str(exc)) from exc
    return await _generate_avatar_step(
        db,
        user_id,
        avatar_prompt=avatar_prompt,
        style=_DEFAULT_STYLE,
        persist=persona.is_portrait_confirmed,
    )


async def get_active_avatar(db: AsyncSession, user_id: int) -> AvatarAsset | None:
    asset = (
        await db.execute(select(AvatarAsset).where(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)))
    ).scalar_one_or_none()
    if asset is not None:
        db.expunge(asset)
        _re_sign_avatar_url(asset)
    return asset


async def select_avatar(db: AsyncSession, user_id: int, avatar_id: int) -> AvatarAsset:
    """将指定头像设为激活态，并取消该用户其余头像的激活。"""
    # DESIGN §5.4 形象锁定：锁定后切换激活头像等于换掉已确认的视觉身份（
    # 生成的模型与外观仍指向原形象行），与重生路径同罪，协议直连也要拒绝
    persona = await get_or_create_persona(db, user_id)
    await raise_if_image_sealed(db, user_id, persona)
    asset = (
        await db.execute(select(AvatarAsset).where(AvatarAsset.id == avatar_id, AvatarAsset.user_id == user_id))
    ).scalar_one_or_none()
    if asset is None:
        raise AvatarNotFoundError(f"avatar {avatar_id} not found")
    await db.execute(
        update(AvatarAsset).where(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)).values(active=False),
    )
    asset.active = True
    await db.commit()
    await db.refresh(asset)
    db.expunge(asset)
    _re_sign_avatar_url(asset)
    return asset


async def list_avatar_history(db: AsyncSession, user_id: int, limit: int = 20) -> list[AvatarAsset]:
    assets = (
        (
            await db.execute(
                select(AvatarAsset)
                .where(AvatarAsset.user_id == user_id)
                .order_by(AvatarAsset.created_at.desc())
                .limit(limit),
            )
        )
        .scalars()
        .all()
    )
    survivors: list[AvatarAsset] = []
    for asset in assets:
        if _is_orphan_temp_media_asset(asset):
            await db.delete(asset)
            continue
        db.expunge(asset)
        _re_sign_avatar_url(asset)
        survivors.append(asset)
    if len(survivors) != len(assets):
        await db.commit()
    return survivors


def _is_orphan_temp_media_asset(asset: AvatarAsset) -> bool:
    """头像草稿的所有图片字段都指向已过期的 temp-media 文件 → DB 行已无可用图片，按孤儿清理。

    字段全空、或任一字段还有活着的 temp-media / 已是 companion-avatars，都不视作孤儿。
    """
    has_temp_ref = False
    for attr in _AVATAR_IMAGE_FIELDS:
        val = getattr(asset, attr, None)
        if not val:
            continue
        if not val.startswith("temp-media/"):
            return False
        has_temp_ref = True
        file_id = val.split("/", 1)[1]
        if get_file_path(file_id) is not None:
            return False
    return has_temp_ref


def re_sign_bare_path(bare_path: str | None) -> str | None:
    """把裸路径重新签名为新鲜 URL；temp-media 草稿则转为 /api/media/files/ 形式。"""
    if not bare_path:
        return None
    if bare_path.startswith("temp-media/"):
        return _temp_media_public_url(bare_path)
    if not bare_path.startswith("companion-avatars/"):
        return None
    filename = bare_path.split("/", 1)[1]
    if "/" in filename or "\\" in filename or ".." in filename:
        return None
    file_id, _, ext = filename.partition(".")
    if not file_id:
        return None
    return build_signed_avatar_url(file_id, ext)


def _re_sign_avatar_url(asset: AvatarAsset) -> None:
    for attr in _AVATAR_IMAGE_FIELDS:
        val = getattr(asset, attr, None)
        if val:
            signed = re_sign_bare_path(val)
            if signed:
                setattr(asset, attr, signed)


async def regenerate_avatar(
    mode: ImageReviseMode,
    db: AsyncSession | None = None,
    user_id: int | None = None,
    persona: Persona | None = None,
    feedback: str | None = None,
    style: str = _DEFAULT_STYLE,
) -> AvatarAsset:
    """根据反馈微调当前头像，或按角色资料重新生成。"""
    if user_id is None:
        raise ValueError("user_id is required")
    persona = await _verified_persona(db, user_id, persona)

    if mode == "edit":
        return await _edit_active_avatar(
            db,
            user_id,
            persona,
            feedback=feedback,
            style=style,
        )

    parts = await _prepare_appearance_parts(user_id, persona)
    if parts is not None:
        avatar_prompt = _avatar_creative_prompt(
            persona,
            appearance=parts.portrait_description,
            feedback=feedback,
        )
    else:
        try:
            avatar_prompt = await enhance_avatar_prompt(db, user_id, persona, feedback=feedback)
        except (ValidationError, RuntimeError) as exc:
            raise AvatarGenerationError("prompt enhancement failed", internal=str(exc)) from exc
    return await _generate_avatar_step(
        db,
        user_id,
        avatar_prompt=avatar_prompt,
        style=style,
        feedback=feedback,
        persist=persona.is_portrait_confirmed,
    )


async def _edit_active_avatar(
    db: AsyncSession | None,
    user_id: int,
    persona: Persona,
    *,
    feedback: str | None,
    style: str,
) -> AvatarAsset:
    """微调当前激活头像：编辑底图即该行 asset_url，成功后照常写入新 AvatarAsset 行。"""
    effective_feedback = (feedback or "").strip()
    if not effective_feedback:
        raise AvatarGenerationError("请先描述要微调的内容")

    async def _current_uri(session: AsyncSession) -> tuple[AvatarAsset | None, str | None]:
        asset = (
            await session.execute(
                select(AvatarAsset).where(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)),
            )
        ).scalar_one_or_none()
        if asset is None:
            return None, None
        return asset, await asyncio.to_thread(load_avatar_bytes_as_data_uri, asset.asset_url)

    if db is not None:
        current, edit_uri = await _current_uri(db)
    else:
        async with SESSION_LOCAL() as probe_db:
            current, edit_uri = await _current_uri(probe_db)
    if current is None:
        raise AvatarNotFoundError("找不到当前头像，请重新生成")
    if not edit_uri:
        raise AvatarSourceUnreadableError("当前头像文件缺失或无法读取，请重新生成")

    parts = await _prepare_appearance_parts(user_id, persona)
    prompt = build_image_edit_prompt(
        effective_feedback,
        preserve=EDIT_PRESERVE_AVATAR,
        appearance_description=parts.portrait_description if parts is not None else "",
    )
    persist = persona.is_portrait_confirmed
    return await _generate_avatar_step(
        db,
        user_id,
        avatar_prompt=prompt,
        style=style,
        feedback=effective_feedback,
        reference_image=edit_uri,
        persist=persist,
        image_edit=True,
    )


def _read_as_data_uri(resolved: tuple[Path, str]) -> str | None:
    """把 (磁盘路径, mime) 读为 data URI；文件缺失/不可读返回 None。"""
    path, mime = resolved
    try:
        return build_data_uri(path.read_bytes(), mime)
    except OSError:
        return None


def load_avatar_bytes_as_data_uri(asset_url_or_path: str | None) -> str | None:
    if not asset_url_or_path:
        return None
    if asset_url_or_path.startswith("data:"):
        return asset_url_or_path

    clean_path = asset_url_or_path.replace("\\", "/")

    # 1. 引导草稿：需经 temp_files 边车元数据解析
    temp_file_id: str | None = None
    if "temp-media/" in clean_path:
        temp_idx = clean_path.find("temp-media/")
        temp_file_id = clean_path[temp_idx + len("temp-media/") :].split("?")[0]
    elif "/api/media/files/" in clean_path:
        idx = clean_path.find("/api/media/files/")
        temp_file_id = clean_path[idx + len("/api/media/files/") :].split("?")[0].split("/")[0]

    if temp_file_id:
        raw_id = temp_file_id.rsplit(".", 1)[0] if "." in temp_file_id else temp_file_id
        if (res := get_file_path(raw_id) or get_file_path(temp_file_id)) is not None and (
            uri := _read_as_data_uri(res)
        ):
            return uri

    # 2. 从裸路径或签名 URL 中提取文件名
    filename: str | None = None
    bare_marker = "companion-avatars/"
    bare_idx = clean_path.find(bare_marker)
    if bare_idx >= 0:
        filename = clean_path[bare_idx + len(bare_marker) :].split("?")[0]
    elif "/api/companion/avatar/file/" in clean_path:
        idx = clean_path.find("/api/companion/avatar/file/")
        path_only = clean_path[idx + len("/api/companion/avatar/file/") :].split("?")[0]
        filename = path_only.rsplit("/", 1)[-1]
    else:
        filename = Path(clean_path.split("?")[0]).name

    if filename:
        if (resolved := resolve_uploaded_avatar_path(filename)) is not None and (uri := _read_as_data_uri(resolved)):
            return uri
        if "." not in filename:
            for ext in ("jpg", "png", "jpeg", "webp"):
                if (resolved := resolve_uploaded_avatar_path(f"{filename}.{ext}")) is not None and (
                    uri := _read_as_data_uri(resolved)
                ):
                    return uri

    # 3. 兜底：按 companion-assets 资产路径解析
    if "companion-assets/" in clean_path or "/api/companion/asset/" in clean_path:
        parts = clean_path.split("?")[0].split("/")
        if len(parts) >= 2:
            try:
                uid = int(parts[-2])
                if (resolved := resolve_companion_asset_path(uid, parts[-1])) is not None and (
                    uri := _read_as_data_uri(resolved)
                ):
                    return uri
            except Exception:
                logger.debug("asset resolution fallback failed for path %s", clean_path, exc_info=True)

    return None


def load_character_reference_data_uri(asset: AvatarAsset) -> str | None:
    """日常出镜读取独立全身参考，不使用头像。"""
    return load_avatar_bytes_as_data_uri(asset.seed_fullbody_url)


async def regenerate_avatar_from_image(
    db: AsyncSession | None = None,
    user_id: int | None = None,
    persona: Persona | None = None,
    data: bytes = b"",
    content_type: str = "image/png",
    description: str | None = None,
    presentation_data: bytes | None = None,
    presentation_content_type: str | None = None,
    style: str = _DEFAULT_STYLE,
) -> AvatarAsset:
    """以用户图锚定身份重绘头像，第二张图仅参考光线、色调与构图。"""
    if user_id is None:
        raise ValueError("user_id is required")
    persona = await _verified_persona(db, user_id, persona)
    parts = await _prepare_appearance_parts(user_id, persona)
    if parts is not None:
        avatar_prompt = _avatar_creative_prompt(
            persona,
            appearance=parts.portrait_description,
            feedback=description,
            reference_image=True,
            presentation_image=presentation_data is not None,
        )
    else:
        try:
            base_description = await enhance_avatar_prompt(
                db,
                user_id,
                persona,
                feedback=description,
                has_reference=True,
            )
        except (ValidationError, RuntimeError) as exc:
            raise AvatarGenerationError("prompt enhancement failed", internal=str(exc)) from exc
        avatar_prompt = build_avatar_reference_prompt(
            appearance_description="",
            personality="",
            description=base_description,
            feedback=description,
            has_presentation_reference=presentation_data is not None,
        )
    secondary_uri = (
        await asyncio.to_thread(build_data_uri, presentation_data, presentation_content_type or "image/png")
        if presentation_data is not None
        else None
    )
    return await _generate_avatar_step(
        db,
        user_id,
        avatar_prompt=avatar_prompt,
        style=style,
        feedback=description,
        reference_image=await asyncio.to_thread(build_data_uri, data, content_type),
        secondary_reference_image=secondary_uri,
        persist=persona.is_portrait_confirmed,
    )


def resolve_uploaded_avatar_path(filename: str) -> tuple[Path, str] | None:
    """为文件下发路由定位磁盘上的头像文件。"""
    name = Path(filename).name
    if "/" in name or "\\" in name or ".." in name:
        return None
    filepath = Path(SETTINGS.data_dir) / "companion-avatars" / name
    if not filepath.exists():
        return None
    ext = filepath.suffix.lstrip(".").lower()
    content_type = next((ct for ct, e in _UPLOAD_EXTS.items() if e == ext), "image/png")
    return filepath, content_type


async def _read_temp_media_bytes(bare_path: str) -> tuple[bytes, str] | None:
    """读取 temp-media 文件字节；文件因 TTL 过期或不可读时返回 None。"""
    temp_file_id = bare_path.split("/", 1)[1]
    res = get_file_path(temp_file_id)
    if res is None:
        return None
    path, content_type = res
    try:
        data = await asyncio.to_thread(path.read_bytes)
    except OSError:
        return None
    return data, content_type


async def finalize_avatar(db: AsyncSession, user_id: int) -> AvatarAsset | None:
    """确认头像；全身草稿只由全身确认入口转正。"""
    asset = (
        await db.execute(select(AvatarAsset).where(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)))
    ).scalar_one_or_none()
    if asset is None:
        return None

    if asset.asset_url.startswith("temp-media/"):
        result = await _read_temp_media_bytes(asset.asset_url)
        if result is None:
            raise AvatarSourceUnreadableError("头像草稿已过期或无法读取，请重新生成")
        new_path, _, _ = await _persist_portrait_bytes(*result)
        try:
            asset.asset_url = new_path
            await db.commit()
        except BaseException:
            await db.rollback()
            delete_portrait_file(new_path)
            raise
        await db.refresh(asset)
    db.expunge(asset)
    _re_sign_avatar_url(asset)
    return asset


def normalize_avatar_url_to_bare(url: str | None) -> str:
    if not url:
        return ""
    clean = url.strip().replace("\\", "/")
    if clean.startswith(("companion-avatars/", "temp-media/")):
        return clean
    if "/api/media/files/" in clean:
        fid = clean.split("/api/media/files/", 1)[1].split("?")[0].split("/")[0]
        return f"temp-media/{fid}"
    if "/api/companion/avatar/file/" in clean:
        filename = clean.split("/api/companion/avatar/file/", 1)[1].split("?")[0].split("/")[0]
        return f"companion-avatars/{filename}"
    return clean


async def _fetch_fullbody_target(
    db: AsyncSession | None,
    user_id: int,
    avatar_id: int,
) -> tuple[AvatarAsset, Persona]:
    """按 id 取回属于该用户的头像行与 persona。"""

    async def _fetch(session: AsyncSession) -> tuple[AvatarAsset, Persona]:
        asset = (
            await session.execute(
                select(AvatarAsset).where(AvatarAsset.id == avatar_id, AvatarAsset.user_id == user_id),
            )
        ).scalar_one_or_none()
        if asset is None:
            raise AvatarNotFoundError(f"avatar {avatar_id} not found")
        persona = await get_or_create_persona(session, user_id)
        return asset, persona

    if db is None:
        async with SESSION_LOCAL() as probe_db:
            return await _fetch(probe_db)
    return await _fetch(db)


async def _prepare_fullbody_reference(
    user_id: int,
    asset: AvatarAsset,
    persona: Persona,
    *,
    feedback: str | None,
    secondary_reference: str | None = None,
    canvas_aspect: str | None = None,
    identity: CharacterCardSnapshot | None = None,
) -> tuple[str, str]:
    """返回头像参考与完整提示词，供内部生成和外部制作共用。"""
    reference = await asyncio.to_thread(load_avatar_bytes_as_data_uri, asset.asset_url)
    if not reference:
        raise AvatarSourceUnreadableError("头像缺失或无法读取，请重新生成头像")
    definition = load_persona_definition(persona)
    species = str(definition.get("biological_type") or "").strip()
    outfit_description = ""
    if asset.is_fullbody_confirmed:
        async with SESSION_LOCAL() as card_db:
            if identity is None:
                identity = await require_character_snapshot(card_db, user_id)
            outfit_description = (
                await card_db.scalar(
                    select(CompanionOutfit.description).where(
                        CompanionOutfit.user_id == user_id,
                        CompanionOutfit.active.is_(True),
                        CompanionOutfit.status == "ready",
                    ),
                )
                or ""
            )
    if asset.is_fullbody_confirmed:
        appearance = ""
    else:
        parts = await _prepare_appearance_parts(user_id, persona)
        appearance = parts.fullbody_description if parts is not None else ""
    personality = str(definition.get("personality") or "").strip()
    body_baseline = (
        {key: getattr(identity.features, key) for key in BodyFeatures.model_fields} if identity is not None else {}
    )
    direction = await describe_character_form(
        user_id,
        species=species,
        appearance=appearance,
        personality=personality,
        feedback=feedback or "",
        outfit_description=outfit_description,
        body_baseline=body_baseline,
        reference_images=(reference, secondary_reference) if secondary_reference else (reference,),
        identity=_portrait_identity(identity),
        allow_body_change=True,
    )
    prompt = build_fullbody_reference_prompt(
        body_direction=direction,
        species=species,
        gender=definition.get("gender", ""),
        appearance=appearance,
        personality=personality,
        feedback=feedback,
        has_user_reference=bool(secondary_reference),
        body_baseline=body_baseline,
        outfit_description=outfit_description,
        canvas_aspect=canvas_aspect,
    )
    prompt += "\n" + _portrait_identity(identity)
    return reference, prompt


async def _install_fullbody_seed(
    user_id: int,
    *,
    avatar_id: int,
    url: str,
    prompt: str | None,
    identity: CharacterCardSnapshot | None = None,
) -> AvatarAsset:
    """调用方持用户锁；提交新种子后清理旧图，失败时回收未采纳产物。"""
    async with SESSION_LOCAL() as session:
        try:
            target = await session.scalar(
                select(AvatarAsset).where(
                    AvatarAsset.id == avatar_id,
                    AvatarAsset.user_id == user_id,
                    AvatarAsset.active.is_(True),
                ),
            )
            if target is None:
                raise AvatarNotFoundError("当前角色已切换，请重新打开全身参考图")
            if identity is not None and not await character_snapshot_is_current(session, user_id, identity):
                raise AvatarGenerationError("角色卡已更新，请重新生成全身形象")
            previous_url = target.seed_fullbody_url
            raw = safe_json_loads(target.prompt_json, default={})
            payload = raw if isinstance(raw, dict) else {}
            if prompt is None:
                payload.pop("fullbody_reference_prompt", None)
            else:
                payload["fullbody_reference_prompt"] = prompt
            target.prompt_json = json.dumps(payload, ensure_ascii=False)
            target.seed_fullbody_url = url
            await session.commit()
        except BaseException:
            await session.rollback()
            delete_portrait_file(url)
            raise
        if previous_url and previous_url != url:
            delete_portrait_file(previous_url)
        session.expunge(target)
        _re_sign_avatar_url(target)
        return target


async def generate_fullbody_reference(
    user_id: int,
    *,
    avatar_id: int,
    mode: ImageReviseMode,
    feedback: str | None = None,
    reference_image: str | None = None,
    reference_content_type: str | None = None,
    candidate_id: int | None = None,
) -> AvatarAsset | FullbodyCandidateResponse:
    """生成或编辑当前全身种子。"""
    async with get_avatar_job_lock(user_id):
        asset, persona = await _fetch_fullbody_target(None, user_id, avatar_id)
        if not asset.active:
            raise AvatarNotFoundError("请先选择当前角色的头像")
        async with SESSION_LOCAL() as card_db:
            identity = await require_character_snapshot(card_db, user_id) if asset.is_fullbody_confirmed else None
        effective_feedback = (feedback or "").strip()
        secondary_reference = None
        base_fullbody_url = asset.seed_fullbody_url
        if mode == "edit":
            if reference_image:
                raise AvatarGenerationError("参考图只用于重新生成，微调请先移除参考图")
            if not effective_feedback:
                raise AvatarGenerationError("请先描述要微调的内容")
            edit_path = asset.seed_fullbody_url
            if candidate_id is not None:
                if identity is None:
                    raise AvatarGenerationError("首次全身形象尚无候选图可微调")
                async with SESSION_LOCAL() as candidate_db:
                    candidate = await candidate_db.scalar(
                        select(FullbodyCandidate).where(
                            FullbodyCandidate.id == candidate_id,
                            FullbodyCandidate.user_id == user_id,
                            FullbodyCandidate.avatar_id == avatar_id,
                        ),
                    )
                    if (
                        candidate is None
                        or candidate.status not in ("ready", "failed")
                        or candidate.base_revision != identity.revision
                        or candidate.base_fullbody_url != base_fullbody_url
                    ):
                        raise AvatarGenerationError("全身候选图已变化，请刷新后再微调")
                    edit_path = candidate.image_url
            reference_uri = await asyncio.to_thread(load_avatar_bytes_as_data_uri, edit_path)
            if not reference_uri:
                raise AvatarSourceUnreadableError("上一版全身参考缺失或无法读取，请先重新生成")
            appearance_description = ""
            if not asset.is_fullbody_confirmed:
                parts = await _prepare_appearance_parts(user_id, persona)
                appearance_description = parts.fullbody_description if parts is not None else ""
            prompt = build_image_edit_prompt(
                effective_feedback,
                preserve=EDIT_PRESERVE_FULLBODY + "\n" + _portrait_identity(identity),
                appearance_description=appearance_description,
            )
        else:
            secondary_reference = (
                f"data:{reference_content_type or 'image/png'};base64,{reference_image}" if reference_image else None
            )
            reference_uri, prompt = await _prepare_fullbody_reference(
                user_id,
                asset,
                persona,
                feedback=effective_feedback,
                secondary_reference=secondary_reference,
                identity=identity,
            )
        try:
            generated_url, _, _, _ = await _generate_one_portrait_with_moderation_retry(
                prompt,
                user_id,
                reference_image=reference_uri,
                secondary_reference_image=secondary_reference,
                size=_FULLBODY_SIZE,
                persist=asset.is_fullbody_confirmed,
                image_edit=mode == "edit",
            )
        except AvatarGenerationError as exc:
            raise FullbodyGenerationError(str(exc), internal=exc.internal) from exc

        if identity is not None:
            return await _save_fullbody_candidate(
                user_id,
                avatar_id,
                generated_url,
                identity,
                base_fullbody_url,
            )
        return await _install_fullbody_seed(
            user_id,
            avatar_id=avatar_id,
            url=generated_url,
            prompt=prompt,
            identity=identity,
        )


async def confirm_fullbody_seed(user_id: int, *, avatar_id: int, expected_url: str) -> AvatarAsset:
    """确认当前全身草稿、锁定身份并保存默认外观快照；重试不重复建外观。"""
    async with get_avatar_job_lock(user_id), SESSION_LOCAL() as db:
        asset, persona = await _fetch_fullbody_target(db, user_id, avatar_id)
        await db.refresh(persona, with_for_update=True)
        if not asset.active or not persona.is_portrait_confirmed:
            raise AvatarGenerationError("请先确认当前头像")
        if asset.is_fullbody_confirmed:
            db.expunge(asset)
            _re_sign_avatar_url(asset)
            return asset
        if not asset.seed_fullbody_url or normalize_avatar_url_to_bare(expected_url) != asset.seed_fullbody_url:
            raise AvatarSourceUnreadableError("全身形象已变更，请重新加载后确认")
        uri = await asyncio.to_thread(load_avatar_bytes_as_data_uri, asset.seed_fullbody_url)
        if not uri:
            raise AvatarSourceUnreadableError("全身形象缺失或已过期，请重新生成")
        # 默认外观拥有独立文件，后续重绘种子不能删除既有外观或改变在途视频参考。
        raw = await asyncio.to_thread(base64.b64decode, uri.split(",", 1)[1], validate=True)
        content_type = uri.split(";", 1)[0].removeprefix("data:")
        seed_path: str | None = None
        outfit_path: str | None = None
        try:
            if asset.seed_fullbody_url.startswith("temp-media/"):
                seed_path, _, _ = await _persist_portrait_bytes(raw, content_type)
                asset.seed_fullbody_url = seed_path
            outfit_path, _, _ = await _persist_portrait_bytes(raw, content_type)
            asset.is_fullbody_confirmed = True
            register_character_card(db, asset)
            outfit = CompanionOutfit(
                user_id=user_id,
                name="默认外观",
                fullbody_url=outfit_path,
                status="ready",
                active=True,
                is_initial=True,
                source_json=json.dumps({"identity_reference_path": asset.seed_fullbody_url}, ensure_ascii=False),
            )
            db.add(outfit)
            await db.flush()
            emit_ws_event(
                db,
                user_id=user_id,
                event_type="companion.outfit.updated",
                payload={"outfit_id": outfit.id, "worn": True},
            )
            await db.commit()
        except BaseException:
            await db.rollback()
            for path in (seed_path, outfit_path):
                if path:
                    delete_portrait_file(path)
            raise
        await db.refresh(asset)
        db.expunge(asset)
        _re_sign_avatar_url(asset)
        return asset


async def prepare_fullbody_prompt(
    user_id: int,
    *,
    avatar_id: int,
    feedback: str | None = None,
) -> str:
    """自备图与 AI 使用相同的视觉判断和全身模板。"""
    asset, persona = await _fetch_fullbody_target(None, user_id, avatar_id)
    if not asset.active:
        raise AvatarNotFoundError("请先选择当前角色的头像")
    _, prompt = await _prepare_fullbody_reference(
        user_id,
        asset,
        persona,
        feedback=feedback,
        canvas_aspect=_FULLBODY_ASPECT,
    )
    return prompt


async def adopt_fullbody_seed(
    user_id: int,
    *,
    avatar_id: int,
    data: bytes,
    content_type: str,
) -> AvatarAsset | FullbodyCandidateResponse:
    """安装用户确认的全身图；未确认身份时保留草稿，已确认时不改变既有外观。"""
    if not data:
        raise ValueError("image data is required")
    async with get_avatar_job_lock(user_id):
        asset, _persona = await _fetch_fullbody_target(None, user_id, avatar_id)
        if not asset.active:
            raise AvatarNotFoundError("请先选择当前角色的头像")
        url, _, _ = await _persist_portrait_or_draft(data, user_id, content_type, persist=asset.is_fullbody_confirmed)
        if asset.is_fullbody_confirmed:
            async with SESSION_LOCAL() as db:
                identity = await require_character_snapshot(db, user_id)
            return await _save_fullbody_candidate(user_id, avatar_id, url, identity, asset.seed_fullbody_url)
        return await _install_fullbody_seed(
            user_id,
            avatar_id=avatar_id,
            url=url,
            prompt=None,
        )


async def adopt_avatar_seed(
    user_id: int,
    *,
    data: bytes,
    content_type: str,
) -> AvatarAsset:
    """直接使用用户上传的图片作为头像，跳过 AI 生成。"""
    if not data:
        raise ValueError("image data is required")
    persona = await _verified_persona(None, user_id, None)
    persist = persona.is_portrait_confirmed
    asset_url, file_id, final_ext = await _persist_portrait_or_draft(data, user_id, content_type, persist=persist)
    async with SESSION_LOCAL() as session:
        return await _write_avatar_step(
            session,
            user_id,
            asset_url=asset_url,
            file_id=file_id,
            final_ext=final_ext,
            avatar_source_url=_temp_media_public_url(asset_url),
            avatar_prompt="用户上传头像",
            style="custom",
            persist=persist,
        )


async def prepare_avatar_prompt(user_id: int, *, feedback: str | None = None, has_reference: bool = False) -> str:
    """头像自备图提示词；有参考时明确其身份用途，无图时按开放角色描述生成。"""
    persona = await _verified_persona(None, user_id, None)
    parts = await _prepare_appearance_parts(user_id, persona)
    if parts is not None:
        return _avatar_creative_prompt(
            persona,
            appearance=parts.portrait_description,
            feedback=feedback,
            reference_image=has_reference,
        )
    prompt = await enhance_avatar_prompt(None, user_id, persona, feedback=feedback, has_reference=has_reference)
    if not has_reference:
        return prompt
    return build_avatar_reference_prompt(
        appearance_description="",
        personality="",
        description=prompt,
        feedback=feedback,
    )
