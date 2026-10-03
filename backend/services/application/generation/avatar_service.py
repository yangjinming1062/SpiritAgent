import asyncio
import contextlib
import json
from collections.abc import Awaitable, Callable
from pathlib import Path

from components import (
    SESSION_LOCAL,
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
    OutfitSource,
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
from pydantic import BaseModel, ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from services.domains.companion import (
    CharacterCardNotReadyError,
    character_snapshot_is_current,
    emit_character_card_updated,
    get_character_card,
    get_or_create_persona,
    load_persona_definition,
    register_character_card,
    require_character_snapshot,
)
from services.infrastructure.assets import (
    build_data_uri,
    normalize_asset_reference,
    read_asset_data_uri,
    resolve_asset_reference,
    save_companion_asset_async,
    signed_companion_asset_url,
)
from services.infrastructure.llm import (
    SIZE_TO_ASPECT,
    LLMRuntimeError,
    chat,
    is_content_policy_error_message,
    vision_chat,
)

from .appearance_prompts import (
    build_avatar_reference_prompt,
    build_image_edit_prompt,
    describe_character_form,
    enhance_avatar_prompt,
)
from .character_images import image_asset_bytes
from .fullbody_reference_prompt import build_fullbody_reference_prompt
from .image_generation import ImageGenerationError, generate_images

logger = get_logger(__name__)

_DEFAULT_STYLE: str = "portrait"
_AVATAR_SIZE: str = "1024x1024"
# 全身种子竖版画幅（PIPELINE「头像与全身」）：9:16，作为参考立绘与视频链身份的输入；外观、冻结参考校准与动作姿态图沿用同一画幅。
FULLBODY_SIZE: str = "1024x1792"
FULLBODY_ASPECT: str = SIZE_TO_ASPECT[FULLBODY_SIZE]
_CARD_ANALYSIS_TIMEOUT = 180
_UPLOAD_EXTS: dict[str, str] = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}
ALLOWED_AVATAR_UPLOAD_MIME_TYPES: frozenset[str] = frozenset(_UPLOAD_EXTS)

# 按用户加锁，避免 REST 头像路由与 WS RPC 并发再生成/选择时抢同一行
_AVATAR_JOB_LOCKS: dict[int, asyncio.Lock] = {}


async def _sanitize_prompt_for_moderation(user_id: int, prompt: str) -> str:
    """合规改写被审核拒绝的提示词，失败时返回原文。"""
    try:
        raw = await chat(user_id, MODERATION_SANITIZATION_PROMPT, prompt)
        payload = parse_llm_json(raw)
        sanitized = payload.get("prompt") if isinstance(payload, dict) and set(payload) == {"prompt"} else None
        if isinstance(sanitized, str) and sanitized.strip():
            return sanitized.strip()
        if payload == {"prompt": None}:
            logger.info("prompt sanitization declined by model; keeping original prompt", extra={"user_id": user_id})
        else:
            logger.warning(
                "prompt sanitization output invalid; keeping original prompt",
                extra={"user_id": user_id, "output_chars": len(raw)},
            )
        return prompt
    except Exception:
        # 失败回退到原文属设计意图：内容审核改写是可选优化；保留 exc_info 供排查 LLM/网络层问题。
        logger.warning(
            "prompt sanitization LLM call failed; falling back to original prompt",
            extra={"user_id": user_id},
            exc_info=True,
        )
        return prompt


async def generate_with_moderation_retry[T](user_id: int, prompt: str, generate: Callable[[str], Awaitable[T]]) -> T:
    """命中内容审核时用合规改写后的提示词重试一次；结果未知或改写无变化时保留原错误。"""
    try:
        return await generate(prompt)
    except ImageGenerationError as exc:
        if exc.result_unknown or not is_content_policy_error_message(exc.internal):
            raise
        logger.info("image generation blocked by moderation, sanitizing prompt", extra={"user_id": user_id})
        sanitized = await _sanitize_prompt_for_moderation(user_id, prompt)
        if sanitized == prompt:
            raise
        first_internal = exc.internal
    try:
        return await generate(sanitized)
    except ImageGenerationError as exc:
        if not is_content_policy_error_message(exc.internal):
            raise
        raise ImageGenerationError(
            "生成请求被内容审核拦截，请调整描述后重试",
            internal=f"original: {first_internal}; retry: {exc.internal}",
        ) from exc


class AvatarGenerationError(RuntimeError):
    """形象生成失败；str(exc) 恒为可展示的公开文案，上游原始错误只放在 internal 供日志与流程判断。"""

    def __init__(self, public: str, internal: str = "") -> None:
        super().__init__(public)
        self.internal = internal or public


class AvatarNotFoundError(AvatarGenerationError):
    """目标头像行不存在或不属于调用者。"""


class FullbodyGenerationError(AvatarGenerationError):
    """全身图生成失败。"""


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
    features = {key: getattr(identity.features, key) for key in PortraitFeatures.model_fields}
    return PORTRAIT_IDENTITY_TEMPLATE.format(features=json.dumps(features, ensure_ascii=False))


async def extract_card_features[F: BaseModel](user_id: int, source_path: str, model: type[F]) -> F:
    """按角色卡字段分析立绘裸路径，返回特征；缺字段视为失败，空字段允许未知特征。"""
    loaded = await asyncio.to_thread(read_portrait_bytes, source_path)
    if loaded is None:
        raise ValueError("source image is unreadable")
    data, mime = loaded
    uri = await asyncio.to_thread(build_data_uri, data, mime)
    schema = model.model_json_schema()
    schema["required"] = list(model.model_fields)
    source = "portrait" if model is PortraitFeatures else "body"
    async with asyncio.timeout(_CARD_ANALYSIS_TIMEOUT):
        raw = await vision_chat(
            user_id,
            CHARACTER_CARD_EXTRACTION,
            json.dumps({"source": source, "schema": schema}, ensure_ascii=False),
            reference_images=(uri,),
        )
    payload = parse_llm_json(raw)
    if not isinstance(payload, dict) or set(payload) != set(model.model_fields):
        raise ValueError("incomplete character extraction")
    return model.model_validate(payload)


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


async def _analyze_fullbody_candidate(user_id: int, candidate_id: int, path: str) -> FullbodyCandidateResponse:
    """分析待定或失败候选的身体特征；写回前核对候选图未变。"""
    features: BodyFeatures | None = None
    try:
        features = await extract_card_features(user_id, path, BodyFeatures)
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
        if row.status not in ("pending", "failed"):
            raise AvatarNotFoundError("全身候选图不存在或已采纳")
        path = row.image_url
    return await _analyze_fullbody_candidate(user_id, candidate_id, path)


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
    return await _analyze_fullbody_candidate(user_id, candidate_id, image_url)


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
        if card.status != "ready":
            # 分析成功必然递增修订，分析中或失败时的候选都已无法采纳。
            raise CharacterCardNotReadyError(
                "角色资料正在分析或分析失败，该候选图已无法采纳，请在角色卡完成分析后重新生成全身图",
            )
        if card.revision != row.base_revision or avatar.seed_fullbody_url != row.base_fullbody_url:
            raise AvatarGenerationError("角色资料已更新，请重新生成全身图")
        if await asyncio.to_thread(_portrait_file, row.image_url) is None:
            raise AvatarSourceUnreadableError("全身候选图已无法读取，请重新生成")
        body = BodyFeatures.model_validate_json(row.body_features_json)
        # 采纳只替换身体特征；头像特征与覆盖沿用，身体覆盖随新图清除。
        card.automatic_json = (
            CharacterFeatures.model_validate_json(card.automatic_json)
            .model_copy(update=body.model_dump())
            .model_dump_json()
        )
        card.overrides_json = CharacterOverrides.model_validate_json(card.overrides_json).model_dump_json(
            include=set(PortraitFeatures.model_fields),
            exclude_none=True,
        )
        card.body_result_json = body.model_dump_json()
        card.body_source_path = row.image_url
        card.body_status = "ready"
        card.revision += 1
        card.error = None
        avatar.seed_fullbody_url = row.image_url
        row.status = "accepted"
        emit_character_card_updated(db, card)
        await db.commit()
        return avatar


class AvatarSourceUnreadableError(AvatarGenerationError):
    """头像文件已无法从磁盘读取，需用户重新生成后再重试。"""


class ImageSealedError(AvatarGenerationError):
    """形象已确认锁定：头像重生关闭，全身种子仍可在保持身份的前提下重绘。"""


async def raise_if_image_sealed(db: AsyncSession, user_id: int, persona: Persona) -> None:
    """以激活头像的全身确认标志锁定身份，客户端隐藏入口不能代替服务端守卫。"""
    if not (persona.is_complete and persona.is_portrait_confirmed):
        return
    asset = await get_active_avatar(db, user_id)
    if asset is not None and asset.is_fullbody_confirmed:
        raise ImageSealedError("形象已确认锁定，无法重新生成")


def get_avatar_job_lock(user_id: int) -> asyncio.Lock:
    """惰性创建并返回用户级锁；条目不回收（锁很小且 user_id 空间有限）。"""
    return _AVATAR_JOB_LOCKS.setdefault(user_id, asyncio.Lock())


async def persist_portrait_bytes(user_id: int, data: bytes, content_type: str) -> str:
    """写入用户资产目录的永久立绘并返回裸路径。"""
    ext = _UPLOAD_EXTS.get(content_type.split(";", maxsplit=1)[0].strip().lower(), "jpg")
    return await save_companion_asset_async(data, user_id=user_id, label="portrait", ext=ext)


async def persist_portrait_or_draft(
    data: bytes,
    user_id: int,
    content_type: str,
    *,
    persist: bool,
) -> str:
    """按确认状态保存永久立绘或临时草稿，返回裸路径。"""
    if persist:
        return await persist_portrait_bytes(user_id, data, content_type)
    src_content_type = content_type.split(";", maxsplit=1)[0].strip().lower()
    final_ext = _UPLOAD_EXTS.get(src_content_type, "jpg")
    file_id, _public_url = await asyncio.to_thread(save_file, data, src_content_type, final_ext, user_id=user_id)
    return f"temp-media/{file_id}"


async def _generate_portrait(
    prompt: str,
    user_id: int,
    *,
    persist: bool,
    reference_image: str | None = None,
    secondary_reference_image: str | None = None,
    size: str = _AVATAR_SIZE,
    image_edit: bool = False,
) -> str:
    """生成一张立绘并返回裸路径：persist=False 留作 temp-media 草稿，True 落盘用户资产目录。``image_edit`` 以参考图为底图并按编辑能力过滤，不接受 secondary。"""

    async def generate(text: str) -> list[str]:
        return await generate_images(
            text,
            user_id=user_id,
            size=size,
            reference_image=reference_image,
            secondary_reference_image=secondary_reference_image,
            image_edit=image_edit,
        )

    try:
        urls = await generate_with_moderation_retry(user_id, prompt, generate)
    except ImageGenerationError as exc:
        logger.warning("portrait image generation failed", extra={"user_id": user_id, "error": exc.internal})
        # ImageGenerationError 的 str 按契约是公开文案（含编辑能力缺失等可行动指引），透传不替换。
        raise AvatarGenerationError(str(exc), internal=exc.internal) from exc
    try:
        data, content_type = await image_asset_bytes(urls[0])
    except Exception as exc:
        raise AvatarGenerationError("生成结果下载失败，请稍后重试", internal=str(exc)) from exc
    return await persist_portrait_or_draft(data, user_id, content_type, persist=persist)


async def _write_avatar_step(
    user_id: int,
    *,
    asset_url: str,
    avatar_prompt: str,
    style: str,
    persist: bool,
    feedback: str | None = None,
    reference_image: str | None = None,
    secondary_reference_image: str | None = None,
) -> AvatarAsset:
    """以新头像行替换激活头像并撤销头像确认；已确认身份的旧立绘在提交后删除。"""
    prompt_payload: dict[str, str] = {"avatar_prompt": avatar_prompt, "style": style}
    if feedback is not None:
        prompt_payload["feedback"] = feedback
    if reference_image is not None:
        # 审计行只留 data URI 前缀标记，不落 base64 大字段
        prompt_payload["reference_image"] = reference_image.split(",", 1)[0]
    if secondary_reference_image is not None:
        prompt_payload["secondary_reference_image"] = secondary_reference_image.split(",", 1)[0]
    async with SESSION_LOCAL() as db:
        try:
            previous_url = await db.scalar(
                update(AvatarAsset)
                .where(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True))
                .values(active=False)
                .returning(AvatarAsset.asset_url)
                .execution_options(synchronize_session=False),
            )
            asset = AvatarAsset(
                user_id=user_id,
                prompt_json=json.dumps(prompt_payload, ensure_ascii=False),
                asset_url=asset_url,
                active=True,
            )
            await db.execute(
                update(Persona)
                .where(Persona.user_id == user_id)
                .values(is_portrait_confirmed=False, portrait_confirmed_at=None),
            )
            db.add(asset)
            await db.commit()
        except BaseException:
            await db.rollback()
            delete_portrait_file(asset_url)
            raise

    if persist and previous_url is not None:
        delete_portrait_file(previous_url)
    return asset


async def _generate_avatar_step(
    user_id: int,
    *,
    avatar_prompt: str,
    persist: bool,
    feedback: str | None = None,
    reference_image: str | None = None,
    secondary_reference_image: str | None = None,
    image_edit: bool = False,
) -> AvatarAsset:
    """先在会话外完成立绘生成，再用一次短写会话提交新的 active AvatarAsset 行。"""
    asset_url = await _generate_portrait(
        avatar_prompt,
        user_id,
        persist=persist,
        reference_image=reference_image,
        secondary_reference_image=secondary_reference_image,
        image_edit=image_edit,
    )
    return await _write_avatar_step(
        user_id,
        asset_url=asset_url,
        avatar_prompt=avatar_prompt,
        style=_DEFAULT_STYLE,
        persist=persist,
        feedback=feedback,
        reference_image=reference_image,
        secondary_reference_image=secondary_reference_image,
    )


def _portrait_file(path: str | None) -> tuple[Path, str] | None:
    """定位立绘裸路径：temp-media 草稿或 companion-assets 用户资产；缺失或过期返回 None。"""
    return resolve_asset_reference(path)


def read_portrait_bytes(path: str | None) -> tuple[bytes, str] | None:
    """读取立绘裸路径的字节与类型；文件缺失、过期或不可读时返回 None。"""
    resolved = _portrait_file(path)
    if resolved is None:
        return None
    try:
        return resolved[0].read_bytes(), resolved[1]
    except OSError:
        return None


def load_avatar_bytes_as_data_uri(path: str | None) -> str | None:
    """立绘裸路径读为供应商可内联的 data URI；不可读时返回 None。"""
    return read_asset_data_uri(path)


def delete_portrait_file(path: str | None) -> None:
    """尽力删除立绘文件：temp-media 草稿或 companion-assets 用户资产。"""
    resolved = _portrait_file(path)
    if resolved is not None:
        with contextlib.suppress(OSError):
            resolved[0].unlink(missing_ok=True)


def re_sign_bare_path(bare_path: str | None) -> str | None:
    """响应出口把立绘裸路径签名为新鲜 URL；temp-media 草稿转为 /api/media/files/ 免签名地址。"""
    if not bare_path:
        return None
    if bare_path.startswith("temp-media/"):
        return f"/api/media/files/{bare_path.removeprefix('temp-media/')}"
    return signed_companion_asset_url(bare_path)


def normalize_avatar_url_to_bare(url: str | None) -> str:
    """客户端回传的立绘地址（签名 URL 或草稿地址）还原为裸路径，供与存储值比对。"""
    return normalize_asset_reference(url)


async def _verified_persona(user_id: int) -> Persona:
    """头像入口共用的前置校验：重读 persona（缺则建），onboarding 完成且形象未锁定；锁外读到的快照可能已过期，不接收。"""
    async with SESSION_LOCAL() as db:
        persona = await get_or_create_persona(db, user_id)
        if not persona.is_complete:
            raise AvatarGenerationError("请先完成引导再生成形象", internal="persona is incomplete")
        await raise_if_image_sealed(db, user_id, persona)
    return persona


async def _avatar_description(
    user_id: int,
    persona: Persona,
    feedback: str | None,
    *,
    has_reference: bool = False,
) -> str:
    try:
        return await enhance_avatar_prompt(user_id, persona, feedback=feedback, has_reference=has_reference)
    except (ValidationError, RuntimeError, LLMRuntimeError) as exc:
        raise AvatarGenerationError("形象描述生成失败，请稍后重试", internal=str(exc)) from exc


async def generate_avatar(user_id: int, *, feedback: str | None = None) -> AvatarAsset:
    """按角色资料与本次要求生成头像。"""
    async with get_avatar_job_lock(user_id):
        return await _generate_avatar(user_id, feedback=feedback)


async def _generate_avatar(user_id: int, *, feedback: str | None = None) -> AvatarAsset:
    persona = await _verified_persona(user_id)
    return await _generate_avatar_step(
        user_id,
        avatar_prompt=await _avatar_description(user_id, persona, feedback),
        persist=persona.is_portrait_confirmed,
        feedback=feedback,
    )


async def get_active_avatar(db: AsyncSession, user_id: int) -> AvatarAsset | None:
    return await db.scalar(select(AvatarAsset).where(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)))


async def select_avatar(db: AsyncSession, user_id: int, avatar_id: int) -> AvatarAsset:
    """将指定头像设为激活态，并取消该用户其余头像的激活；换用另一张头像时撤销头像确认。"""
    # 切换头像会改变已确认身份，同样受身份锁保护。
    persona = await get_or_create_persona(db, user_id)
    await raise_if_image_sealed(db, user_id, persona)
    asset = await db.scalar(select(AvatarAsset).where(AvatarAsset.id == avatar_id, AvatarAsset.user_id == user_id))
    if asset is None:
        raise AvatarNotFoundError("找不到对应的形象", internal=f"avatar {avatar_id} not found")
    if not asset.active:
        # 与生成新头像一致：确认只对当时的头像有效，换用的头像须重新确认后才能锁定身份。
        persona.is_portrait_confirmed = False
        persona.portrait_confirmed_at = None
    await db.execute(
        update(AvatarAsset).where(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)).values(active=False),
    )
    asset.active = True
    await db.commit()
    return asset


async def list_avatar_history(db: AsyncSession, user_id: int, limit: int = 20) -> list[AvatarAsset]:
    assets = (
        await db.scalars(
            select(AvatarAsset)
            .where(AvatarAsset.user_id == user_id)
            .order_by(AvatarAsset.created_at.desc())
            .limit(limit),
        )
    ).all()
    survivors: list[AvatarAsset] = []
    for asset in assets:
        if _is_orphan_temp_media_asset(asset):
            await db.delete(asset)
            continue
        survivors.append(asset)
    if len(survivors) != len(assets):
        await db.commit()
    return survivors


def _is_orphan_temp_media_asset(asset: AvatarAsset) -> bool:
    """头像草稿所有图片字段都指向已过期 temp-media → DB 行无可用图片，按孤儿清理。字段全空或任一字段仍有活图则不视作孤儿。"""
    paths = [path for path in (asset.asset_url, asset.seed_fullbody_url) if path]
    return bool(paths) and all(path.startswith("temp-media/") and _portrait_file(path) is None for path in paths)


async def regenerate_avatar(mode: ImageReviseMode, user_id: int, feedback: str | None = None) -> AvatarAsset:
    """根据反馈微调当前头像，或按角色资料重新生成。"""
    async with get_avatar_job_lock(user_id):
        if mode == "regenerate":
            return await _generate_avatar(user_id, feedback=feedback)
        persona = await _verified_persona(user_id)
        effective_feedback = (feedback or "").strip()
        if not effective_feedback:
            raise AvatarGenerationError("请先描述要微调的内容")
        # 编辑底图即当前激活头像，成功后照常写入新 AvatarAsset 行。
        async with SESSION_LOCAL() as db:
            current = await get_active_avatar(db, user_id)
        if current is None:
            raise AvatarNotFoundError("找不到当前头像，请重新生成")
        edit_uri = await asyncio.to_thread(load_avatar_bytes_as_data_uri, current.asset_url)
        if not edit_uri:
            raise AvatarSourceUnreadableError("当前头像文件缺失或无法读取，请重新生成")
        return await _generate_avatar_step(
            user_id,
            avatar_prompt=build_image_edit_prompt(effective_feedback, preserve=EDIT_PRESERVE_AVATAR),
            persist=persona.is_portrait_confirmed,
            feedback=effective_feedback,
            reference_image=edit_uri,
            image_edit=True,
        )


async def regenerate_avatar_from_image(
    user_id: int,
    *,
    data: bytes = b"",
    content_type: str = "image/png",
    description: str | None = None,
    presentation_data: bytes | None = None,
    presentation_content_type: str | None = None,
) -> AvatarAsset:
    """以用户图锚定身份重绘头像，第二张图仅参考光线、色调与构图。"""
    async with get_avatar_job_lock(user_id):
        persona = await _verified_persona(user_id)
        base_description = await _avatar_description(user_id, persona, description, has_reference=True)
        avatar_prompt = build_avatar_reference_prompt(
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
            user_id,
            avatar_prompt=avatar_prompt,
            persist=persona.is_portrait_confirmed,
            feedback=description,
            reference_image=await asyncio.to_thread(build_data_uri, data, content_type),
            secondary_reference_image=secondary_uri,
        )


async def finalize_avatar(db: AsyncSession, user_id: int) -> AvatarAsset | None:
    """确认头像；全身草稿只由全身确认入口转正。"""
    asset = await get_active_avatar(db, user_id)
    if asset is None:
        return None

    if asset.asset_url.startswith("temp-media/"):
        draft = await asyncio.to_thread(read_portrait_bytes, asset.asset_url)
        if draft is None:
            raise AvatarSourceUnreadableError("头像草稿已过期或无法读取，请重新生成")
        new_path = await persist_portrait_bytes(user_id, *draft)
        try:
            asset.asset_url = new_path
            await db.commit()
        except BaseException:
            await db.rollback()
            delete_portrait_file(new_path)
            raise
    elif await asyncio.to_thread(_portrait_file, asset.asset_url) is None:
        # 被替换的已确认头像只删文件、保留历史行；重新选中后不能把缺失的文件确认为头像。
        raise AvatarSourceUnreadableError("头像文件缺失或无法读取，请重新生成")
    return asset


async def _fetch_fullbody_target(db: AsyncSession, user_id: int, avatar_id: int) -> tuple[AvatarAsset, Persona]:
    """按 id 取回属于该用户的头像行与 persona。"""
    asset = await db.scalar(select(AvatarAsset).where(AvatarAsset.id == avatar_id, AvatarAsset.user_id == user_id))
    if asset is None:
        raise AvatarNotFoundError("找不到对应的形象", internal=f"avatar {avatar_id} not found")
    return asset, await get_or_create_persona(db, user_id)


async def _require_ready_character_snapshot(db: AsyncSession, user_id: int) -> CharacterCardSnapshot:
    """已确认身份的全身候选只基于就绪的角色卡：分析中或失败时的候选必然无法采纳，付费生成前拒绝。"""
    card = await get_character_card(db, user_id)
    if card is not None and card.revision > 0:
        if card.status == "failed":
            raise CharacterCardNotReadyError("角色资料分析失败，请先在角色卡中重试分析，再调整全身图")
        if card.status != "ready":
            raise CharacterCardNotReadyError("角色资料正在分析，请待分析完成后再调整全身图")
    return await require_character_snapshot(db, user_id)


async def _load_fullbody_target(
    user_id: int,
    avatar_id: int,
) -> tuple[AvatarAsset, Persona, CharacterCardSnapshot | None]:
    """取回当前激活的目标头像与 persona；已确认身份时同时冻结当前角色卡快照。"""
    async with SESSION_LOCAL() as db:
        asset, persona = await _fetch_fullbody_target(db, user_id, avatar_id)
        if not asset.active:
            raise AvatarNotFoundError("请先选择当前角色的头像")
        identity = await _require_ready_character_snapshot(db, user_id) if asset.is_fullbody_confirmed else None
    return asset, persona, identity


async def _prepare_fullbody_reference(
    user_id: int,
    asset: AvatarAsset,
    persona: Persona,
    identity: CharacterCardSnapshot | None,
    *,
    feedback: str | None,
    secondary_reference: str | None = None,
    canvas_aspect: str | None = None,
) -> tuple[str, str]:
    """返回头像参考与完整提示词，供内部生成和外部制作共用。"""
    reference = await asyncio.to_thread(load_avatar_bytes_as_data_uri, asset.asset_url)
    if not reference:
        raise AvatarSourceUnreadableError("头像缺失或无法读取，请重新生成头像")
    definition = load_persona_definition(persona)
    species = str(definition.get("biological_type") or "").strip()
    outfit_description = ""
    if identity is not None:
        async with SESSION_LOCAL() as db:
            outfit_description = (
                await db.scalar(
                    select(CompanionOutfit.description).where(
                        CompanionOutfit.user_id == user_id,
                        CompanionOutfit.active.is_(True),
                        CompanionOutfit.status == "ready",
                    ),
                )
                or ""
            )
    personality = str(definition.get("personality") or "").strip()
    body_baseline = (
        {key: getattr(identity.features, key) for key in BodyFeatures.model_fields} if identity is not None else {}
    )
    direction = await describe_character_form(
        user_id,
        species=species,
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
        personality=personality,
        feedback=feedback,
        has_user_reference=bool(secondary_reference),
        body_baseline=body_baseline,
        outfit_description=outfit_description,
        canvas_aspect=canvas_aspect,
    )
    prompt += "\n" + _portrait_identity(identity)
    return reference, prompt


async def _install_fullbody_seed(user_id: int, *, avatar_id: int, url: str, prompt: str | None) -> AvatarAsset:
    """首次全身草稿安装；调用方持用户锁，提交后清理旧图，失败时回收未采纳产物。"""
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
        return target


async def generate_fullbody_reference(
    user_id: int,
    *,
    avatar_id: int,
    mode: ImageReviseMode,
    feedback: str | None = None,
    reference_image: bytes | None = None,
    reference_content_type: str | None = None,
    candidate_id: int | None = None,
) -> AvatarAsset | FullbodyCandidateResponse:
    """生成或编辑当前全身种子；已确认身份时产物进入候选，等待采纳。"""
    async with get_avatar_job_lock(user_id):
        asset, persona, identity = await _load_fullbody_target(user_id, avatar_id)
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
            prompt = build_image_edit_prompt(
                effective_feedback,
                preserve=EDIT_PRESERVE_FULLBODY + "\n" + _portrait_identity(identity),
            )
        else:
            if reference_image:
                secondary_reference = await asyncio.to_thread(
                    build_data_uri,
                    reference_image,
                    reference_content_type or "image/png",
                )
            reference_uri, prompt = await _prepare_fullbody_reference(
                user_id,
                asset,
                persona,
                identity,
                feedback=effective_feedback,
                secondary_reference=secondary_reference,
            )
        try:
            generated_url = await _generate_portrait(
                prompt,
                user_id,
                persist=asset.is_fullbody_confirmed,
                reference_image=reference_uri,
                secondary_reference_image=secondary_reference,
                size=FULLBODY_SIZE,
                image_edit=mode == "edit",
            )
        except AvatarGenerationError as exc:
            raise FullbodyGenerationError(str(exc), internal=exc.internal) from exc

        if identity is not None:
            return await _save_fullbody_candidate(user_id, avatar_id, generated_url, identity, base_fullbody_url)
        return await _install_fullbody_seed(user_id, avatar_id=avatar_id, url=generated_url, prompt=prompt)


async def confirm_fullbody_seed(user_id: int, *, avatar_id: int, expected_url: str) -> AvatarAsset:
    """确认当前全身草稿、锁定身份并保存默认外观快照；重试不重复建外观。"""
    async with get_avatar_job_lock(user_id), SESSION_LOCAL() as db:
        asset, persona = await _fetch_fullbody_target(db, user_id, avatar_id)
        await db.refresh(persona, with_for_update=True)
        if not asset.active or not persona.is_portrait_confirmed:
            raise AvatarGenerationError("请先确认当前头像")
        if asset.is_fullbody_confirmed:
            return asset
        # 锁定身份前头像须已确认转存为可读的正式资产，草稿或缺失文件不能成为身份依据。
        if (
            asset.asset_url.startswith("temp-media/")
            or await asyncio.to_thread(_portrait_file, asset.asset_url) is None
        ):
            raise AvatarSourceUnreadableError("头像文件缺失或尚未确认，请重新确认头像")
        if not asset.seed_fullbody_url or normalize_avatar_url_to_bare(expected_url) != asset.seed_fullbody_url:
            raise AvatarSourceUnreadableError("全身形象已变更，请重新加载后确认")
        seed = await asyncio.to_thread(read_portrait_bytes, asset.seed_fullbody_url)
        if seed is None:
            raise AvatarSourceUnreadableError("全身形象缺失或已过期，请重新生成")
        # 默认外观拥有独立文件，后续重绘种子不能删除既有外观或改变在途视频参考。
        seed_path: str | None = None
        outfit_path: str | None = None
        try:
            if asset.seed_fullbody_url.startswith("temp-media/"):
                seed_path = await persist_portrait_bytes(user_id, *seed)
                asset.seed_fullbody_url = seed_path
            outfit_path = await persist_portrait_bytes(user_id, *seed)
            asset.is_fullbody_confirmed = True
            register_character_card(db, asset)
            outfit = CompanionOutfit(
                user_id=user_id,
                name="默认外观",
                fullbody_url=outfit_path,
                status="ready",
                active=True,
                is_initial=True,
                source_json=OutfitSource(identity_reference_path=asset.seed_fullbody_url).dump(),
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
        return asset


async def prepare_fullbody_prompt(
    user_id: int,
    *,
    avatar_id: int,
    feedback: str | None = None,
) -> str:
    """自备图与 AI 使用相同的视觉判断和全身模板。"""
    asset, persona, identity = await _load_fullbody_target(user_id, avatar_id)
    _, prompt = await _prepare_fullbody_reference(
        user_id,
        asset,
        persona,
        identity,
        feedback=feedback,
        canvas_aspect=FULLBODY_ASPECT,
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
        asset, _, identity = await _load_fullbody_target(user_id, avatar_id)
        url = await persist_portrait_or_draft(data, user_id, content_type, persist=asset.is_fullbody_confirmed)
        if identity is not None:
            return await _save_fullbody_candidate(user_id, avatar_id, url, identity, asset.seed_fullbody_url)
        return await _install_fullbody_seed(user_id, avatar_id=avatar_id, url=url, prompt=None)


async def adopt_avatar_seed(
    user_id: int,
    *,
    data: bytes,
    content_type: str,
) -> AvatarAsset:
    """直接使用用户上传的图片作为头像，跳过 AI 生成。"""
    async with get_avatar_job_lock(user_id):
        if not data:
            raise ValueError("image data is required")
        persona = await _verified_persona(user_id)
        persist = persona.is_portrait_confirmed
        return await _write_avatar_step(
            user_id,
            asset_url=await persist_portrait_or_draft(data, user_id, content_type, persist=persist),
            avatar_prompt="用户上传头像",
            style="custom",
            persist=persist,
        )


async def prepare_avatar_prompt(user_id: int, *, feedback: str | None = None, has_reference: bool = False) -> str:
    """头像自备图提示词；有参考时明确其身份用途，无图时按开放角色描述生成。"""
    persona = await _verified_persona(user_id)
    prompt = await enhance_avatar_prompt(user_id, persona, feedback=feedback, has_reference=has_reference)
    if not has_reference:
        return prompt
    return build_avatar_reference_prompt(
        personality="",
        description=prompt,
        feedback=feedback,
    )
