import asyncio
import contextlib
import io
import json

from infrastructure.ai.error_classifier import LLMRuntimeError
from infrastructure.ai.providers._size_aspect import SIZE_TO_ASPECT
from infrastructure.ai.selection import GenerationOrigin, ProviderSelection
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.assets.asset_store import (
    build_data_uri,
    normalize_asset_reference,
    outfit_asset_directory,
    read_asset_data_uri,
    resolve_asset_reference,
    save_companion_asset_async,
    signed_companion_asset_url,
    unlink_companion_asset,
)
from infrastructure.async_io import run_blocking
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from infrastructure.request_errors import RequestError
from infrastructure.temp_files import save_file
from infrastructure.video_processing.ffmpeg import ActionMaterialRejectedError, VideoProcessError
from infrastructure.video_processing.image import prepare_transparent_image
from infrastructure.video_processing.matting import require_matting_model
from infrastructure.video_processing.quality import validate_transparent_image
from PIL import Image
from pydantic import ValidationError
from runtime.events.emit import emit_ws_event
from runtime.paid_work import GenerationWorkPaused, require_new_generation_call
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from features.appearance.asset_bindings import commit_appearance, flush_appearance
from features.appearance.fullbody_reference_prompt import build_fullbody_reference_prompt
from features.appearance.identity import (
    emit_preparation_updated,
    get_visual_identity_asset,
    initialize_visual_identity,
    require_visual_identity,
    visual_identity_is_current,
)
from features.appearance.image_jobs import complete_appearance_image, generate_appearance_image
from features.appearance.models import AvatarAsset, CompanionOutfit, FullbodyCandidate, FullbodyReviewJob, OutfitSource
from features.appearance.prompt_assembly import (
    build_avatar_reference_prompt,
    build_image_edit_prompt,
    describe_character_form,
    enhance_avatar_prompt,
)
from features.appearance.prompts import EDIT_PRESERVE_AVATAR, EDIT_PRESERVE_FULLBODY
from features.appearance.queries import initial_outfit_snapshot
from features.appearance.schemas import FullbodyCandidateResponse, ImageReviseMode
from features.assets.lifecycle import cleanup_user_assets, enqueue_asset_cleanup
from features.companion.persona_service import load_persona_definition
from features.companion.query_contracts import PersonaSnapshot
from features.companion.visual_state import ensure_persona_snapshot, lock_persona_snapshot, reset_portrait_confirmation
from features.jobs.service import bind_job_assets
from features.media.chain import MediaCandidate
from features.media.character_images import image_asset_bytes
from features.media.identity_contracts import VisualIdentitySnapshot
from features.media.identity_review import IdentityReview, review_portrait_identity
from features.media.image_generation import ImageGenerationError
from features.media.manual_reviews import manual_candidate_views
from features.media.review_contracts import (
    ReviewDecision,
    ReviewEvidence,
    VisualReview,
    candidate_review_key,
    review_basis,
)
from features.media.review_evidence import review_media_intact
from features.media.review_operations import review_media_job
from features.media.review_views import MediaJobView, MediaReviewRequest, MediaSlotView, review_request_matches

logger = get_logger(__name__)

_DEFAULT_STYLE: str = "portrait"
_AVATAR_SIZE: str = "1024x1024"
# 全身种子竖版画幅（PIPELINE「头像与全身」）：9:16，作为参考立绘与视频链身份的输入；外观、冻结参考校准与动作姿态图沿用同一画幅。
FULLBODY_SIZE: str = "1024x1792"
FULLBODY_ASPECT: str = SIZE_TO_ASPECT[FULLBODY_SIZE]
_UPLOAD_EXTS: dict[str, str] = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}
ALLOWED_AVATAR_UPLOAD_MIME_TYPES: frozenset[str] = frozenset(_UPLOAD_EXTS)

# 按用户加锁，避免 REST 头像路由与 WS RPC 并发再生成/选择时抢同一行
_AVATAR_JOB_LOCKS: dict[int, asyncio.Lock] = {}


class AvatarGenerationError(RuntimeError):
    """形象生成失败；str(exc) 恒为可展示的公开文案，上游原始错误只放在 internal 供日志与流程判断。"""

    def __init__(self, public: str, internal: str = "") -> None:
        super().__init__(public)
        self.internal = internal or public


class AvatarNotFoundError(AvatarGenerationError):
    """目标头像行不存在或不属于调用者。"""


class FullbodyGenerationError(AvatarGenerationError):
    """全身图生成失败。"""


class PersonaIncompleteError(AvatarGenerationError):
    """引导未完成，形象生成前置条件不满足。"""


class AvatarConflictError(ValueError):
    """激活头像与预期不符，需重新加载预览后再确认。"""


def fullbody_candidate_response(candidate: FullbodyCandidate) -> FullbodyCandidateResponse:
    return FullbodyCandidateResponse(
        id=candidate.id,
        review_job_id=(candidate.review_json or {}).get("job_id"),
        avatar_id=candidate.avatar_id,
        image_url=re_sign_bare_path(candidate.image_url) or candidate.image_url,
        status=candidate.status,
        error=candidate.error,
    )


async def latest_fullbody_candidate(user_id: int, avatar_id: int) -> FullbodyCandidateResponse | None:
    async with SESSION_LOCAL() as db:
        row = await db.scalar(
            select(FullbodyCandidate)
            .where(FullbodyCandidate.user_id == user_id, FullbodyCandidate.avatar_id == avatar_id)
            .order_by(FullbodyCandidate.id.desc())
            .limit(1)
        )
        if row is None or row.status in ("accepted", "rejected"):
            return None
        avatar = await db.get(AvatarAsset, avatar_id)
        identity_asset = await get_visual_identity_asset(db, user_id)
        if (
            avatar is None
            or not avatar.active
            or identity_asset is None
            or row.base_fullbody_url != avatar.seed_fullbody_url
            or row.base_revision != identity_asset.identity_revision
        ):
            return None
        return fullbody_candidate_response(row)


async def _review_fullbody_candidate(
    user_id: int, candidate_id: int, path: str, expected: MediaReviewRequest | None = None
) -> FullbodyCandidateResponse:
    """直接对照确认头像复核候选；允许用户明确调整身体。"""
    async with SESSION_LOCAL() as db:
        current_candidate = await db.get(FullbodyCandidate, candidate_id)
        if current_candidate is None or current_candidate.user_id != user_id or current_candidate.image_url != path:
            raise AvatarGenerationError("候选图已更新，请刷新后重试")
        identity = VisualIdentitySnapshot(
            avatar_id=current_candidate.avatar_id, revision=current_candidate.base_revision
        )
        avatar = await db.get(AvatarAsset, current_candidate.avatar_id)
        if avatar is None:
            raise AvatarNotFoundError("确认形象不存在")
        portrait_path = avatar.asset_url
        if expected is None and not await visual_identity_is_current(db, user_id, identity):
            raise AvatarGenerationError("确认形象或候选图已更新，请刷新后重试")

    async with SESSION_LOCAL() as db:
        review_job = await db.scalar(
            select(FullbodyReviewJob)
            .where(FullbodyReviewJob.candidate_id == candidate_id, FullbodyReviewJob.user_id == user_id)
            .with_for_update()
        )
        if review_job is None:
            review_job = FullbodyReviewJob(
                user_id=user_id,
                candidate_id=candidate_id,
                state="running",
                checkpoint={"portrait_path": portrait_path, "candidate_path": path},
            )
            db.add(review_job)
            await db.flush()
        if expected is not None and not review_request_matches(
            await fullbody_review_view(user_id, review_job.id), expected
        ):
            raise AvatarGenerationError("检查依据或任务版本已变化")
        review_job.state, review_job.stage = "running", "reviewing"
        candidate = await db.get(FullbodyCandidate, candidate_id)
        report_data = (candidate.review_json or {}).get("report")
        saved_report = IdentityReview.model_validate(report_data) if report_data else None
        portrait_path = review_job.checkpoint["portrait_path"]
        review_job_id = review_job.id
        await bind_job_assets(db, user_id, review_job_id, {portrait_path, path}, "review")
        await db.commit()

    async def save_evidence(evidence: ReviewEvidence) -> None:
        async with SESSION_LOCAL() as db:
            candidate = await db.get(FullbodyCandidate, candidate_id, with_for_update=True)
            job = await db.get(FullbodyReviewJob, review_job_id, with_for_update=True)
            candidate.review_json = {
                **(candidate.review_json or {}),
                "job_id": review_job_id,
                "report": IdentityReview(
                    verdict="review", reason="检查尚未完成", evidence=evidence, status="unavailable"
                ).model_dump(mode="json"),
            }
            job.revision += 1
            await bind_job_assets(db, user_id, job.id, {entry.path for entry in evidence.images}, "review")
            await commit_appearance(db, user_id)

    identity_verdict: IdentityReview | None = None
    error: str | None = None
    try:
        reference, candidate = "", ""
        if saved_report is None or saved_report.evidence is None:
            reference, candidate = await asyncio.gather(
                asyncio.to_thread(read_asset_data_uri, portrait_path), asyncio.to_thread(read_asset_data_uri, path)
            )
            if not reference or not candidate:
                raise AvatarSourceUnreadableError("确认头像或候选图片无法读取")

        async def before_review() -> None:
            require_new_generation_call(user_id)

        identity_verdict = await review_portrait_identity(
            user_id,
            reference,
            candidate,
            task=AiTaskKey.appearance,
            before_submit=before_review,
            evidence=saved_report.evidence if saved_report else None,
            save_evidence=save_evidence,
        )
        if identity_verdict.verdict != "pass" or identity_verdict.status != "complete":
            error = identity_verdict.reason
    except GenerationWorkPaused:
        error = "制作已暂停，恢复后可重试图片复核"
    except Exception:
        logger.warning("fullbody candidate review failed", extra={"candidate_id": candidate_id}, exc_info=True)
        error = "图片检查暂未完成，可以查看候选后采用或重新生成"
    async with SESSION_LOCAL() as db:
        row = await db.scalar(
            select(FullbodyCandidate)
            .where(FullbodyCandidate.id == candidate_id, FullbodyCandidate.user_id == user_id)
            .with_for_update()
        )
        if row is None or row.status not in ("pending", "failed") or row.image_url != path:
            raise AvatarGenerationError("全身候选图已变化，请刷新")
        current_identity = await visual_identity_is_current(db, user_id, identity)
        row.status = (
            "pending"
            if identity_verdict is None or identity_verdict.verdict == "review"
            else "failed"
            if error
            else "ready"
            if current_identity
            else "failed"
        )
        job = await db.get(FullbodyReviewJob, review_job_id, with_for_update=True)
        job.state, job.stage = "paused", "review_pending" if row.status == "pending" else "confirmation"
        row.review_json = {
            **(row.review_json or {}),
            "job_id": review_job_id,
            **({"report": identity_verdict.model_dump(mode="json")} if identity_verdict else {}),
        }
        emit_ws_event(db, user_id=user_id, event_type="media.review.updated", payload={"job_id": str(review_job_id)})
        row.error = error if current_identity else "原任务身份已失效，候选和检查依据已保留，不能按当前身份采纳"
        await commit_appearance(db, user_id)
        return fullbody_candidate_response(row)


async def retry_fullbody_candidate_review(
    user_id: int, avatar_id: int, candidate_id: int, request: MediaReviewRequest
) -> FullbodyCandidateResponse:
    async with get_avatar_job_lock(user_id):
        async with SESSION_LOCAL() as db:
            row = await db.scalar(
                select(FullbodyCandidate).where(
                    FullbodyCandidate.id == candidate_id,
                    FullbodyCandidate.user_id == user_id,
                    FullbodyCandidate.avatar_id == avatar_id,
                )
            )
            if row is None:
                raise AvatarNotFoundError("全身候选图不存在")
            if row.status not in ("pending", "failed"):
                raise AvatarNotFoundError("全身候选图不存在或已采纳")
        job_id = (row.review_json or {}).get("job_id")
        if job_id is None:
            raise AvatarGenerationError("原检查任务不可用")
        await review_media_job(user_id, int(job_id), request)
        async with SESSION_LOCAL() as db:
            current = await db.get(FullbodyCandidate, candidate_id)
            return fullbody_candidate_response(current)


async def _save_fullbody_candidate(
    user_id: int,
    avatar_id: int,
    image_url: str,
    identity: VisualIdentitySnapshot,
    base_fullbody_url: str,
    origin: GenerationOrigin | None = None,
    generation_job_id: int | None = None,
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
                or not await visual_identity_is_current(db, user_id, identity)
            ):
                raise AvatarGenerationError("确认形象已更新，请重新生成全身图")
            previous = (
                await db.scalars(
                    select(FullbodyCandidate).where(
                        FullbodyCandidate.user_id == user_id,
                        FullbodyCandidate.avatar_id == avatar_id,
                        FullbodyCandidate.status.in_(("pending", "ready", "failed")),
                    )
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
                generation_origin_json=origin.model_dump(mode="json") if origin else None,
                status="pending",
            )
            db.add(row)
            await enqueue_asset_cleanup(db, user_id, replaced_paths)
            await complete_appearance_image(db, user_id, generation_job_id)
            await commit_appearance(db, user_id)
            candidate_id = row.id
    except BaseException:
        if generation_job_id is None or image_url.startswith("temp-media/"):
            delete_portrait_file(image_url)
        raise
    for path in replaced_paths:
        if path.startswith("temp-media/"):
            delete_portrait_file(path)
    await cleanup_user_assets(user_id)
    return await _review_fullbody_candidate(user_id, candidate_id, image_url)


async def accept_fullbody_candidate(user_id: int, avatar_id: int, candidate_id: int) -> AvatarAsset:
    async with get_avatar_job_lock(user_id):
        async with SESSION_LOCAL() as db:
            image_path = await db.scalar(
                select(FullbodyCandidate.image_url).where(
                    FullbodyCandidate.id == candidate_id,
                    FullbodyCandidate.user_id == user_id,
                    FullbodyCandidate.avatar_id == avatar_id,
                )
            )
        if image_path is None:
            raise AvatarNotFoundError("全身候选图不存在")
        image = await asyncio.to_thread(read_portrait_bytes, image_path)
        if image is None:
            raise AvatarSourceUnreadableError("全身候选图已无法读取，请重新生成")
        try:
            await asyncio.to_thread(validate_transparent_portrait, image[0], require_clean_edges=False)
        except VideoProcessError as exc:
            raise AvatarGenerationError(str(exc), internal=exc.internal or str(exc)) from exc
        async with SESSION_LOCAL() as db:
            row = await db.scalar(
                select(FullbodyCandidate)
                .where(
                    FullbodyCandidate.id == candidate_id,
                    FullbodyCandidate.user_id == user_id,
                    FullbodyCandidate.avatar_id == avatar_id,
                )
                .with_for_update()
            )
            avatar = await db.get(AvatarAsset, avatar_id, with_for_update={"of": AvatarAsset})
            identity_asset = await get_visual_identity_asset(db, user_id, lock=True)
            if (
                row is None
                or avatar is None
                or identity_asset is None
                or avatar.user_id != user_id
                or not avatar.active
                or not avatar.is_fullbody_confirmed
            ):
                raise AvatarNotFoundError("全身候选图或当前角色不存在")
            if row.status != "ready":
                raise AvatarGenerationError("请先完成候选图的图片复核")
            report_data = (row.review_json or {}).get("report")
            report = IdentityReview.model_validate(report_data) if report_data else None
            manual = (
                VisualReview.model_validate(row.review_json["manual_review"])
                if row.review_json.get("manual_review")
                else None
            )
            accepted = (
                manual.verdict == "pass"
                if manual
                else report is not None and report.status == "complete" and report.verdict == "pass"
            )
            evidence = manual.evidence if manual else report.evidence if report else None
            if (
                not accepted
                or evidence is None
                or not await asyncio.to_thread(review_media_intact, evidence, row.image_url)
            ):
                raise AvatarGenerationError("候选检查尚未完成或证据已变化，不能采纳")
            if (
                identity_asset.identity_revision != row.base_revision
                or avatar.seed_fullbody_url != row.base_fullbody_url
            ):
                raise AvatarGenerationError("确认形象已更新，请重新生成全身图")
            if row.image_url != image_path:
                raise AvatarGenerationError("全身候选图已变化，请刷新")
            avatar.seed_fullbody_url = row.image_url
            avatar.fullbody_generation_origin_json = row.generation_origin_json
            avatar.fullbody_review_json = row.review_json
            review_job_id = (row.review_json or {}).get("job_id")
            if review_job_id:
                accepted_review = await db.get(FullbodyReviewJob, review_job_id, with_for_update=True)
                if accepted_review:
                    accepted_review.state, accepted_review.stage = "succeeded", "complete"
            row.status = "accepted"
            await initialize_visual_identity(db, avatar)
            await commit_appearance(db, user_id)
            return avatar


class AvatarSourceUnreadableError(AvatarGenerationError):
    """头像文件已无法从磁盘读取，需用户重新生成后再重试。"""


class ImageSealedError(AvatarGenerationError):
    """形象已确认锁定：头像重生关闭，全身种子仍可在保持身份的前提下重绘。"""


def _raise_if_sealed(persona: PersonaSnapshot, active_asset: AvatarAsset | None) -> None:
    """以激活头像的全身确认标志锁定身份；active_asset 为调用方读到的激活头像。"""
    if (
        persona.is_complete
        and persona.is_portrait_confirmed
        and active_asset is not None
        and active_asset.is_fullbody_confirmed
    ):
        raise ImageSealedError("形象已确认锁定，无法重新生成")


async def raise_if_image_sealed(db: AsyncSession, user_id: int, persona: PersonaSnapshot) -> None:
    """形象锁定则抛；规则不可能触发时跳过 active_asset 读取。"""
    if not (persona.is_complete and persona.is_portrait_confirmed):
        return
    _raise_if_sealed(persona, await get_active_avatar(db, user_id))


def get_avatar_job_lock(user_id: int) -> asyncio.Lock:
    """惰性创建并返回用户级锁；条目不回收（锁很小且 user_id 空间有限）。"""
    return _AVATAR_JOB_LOCKS.setdefault(user_id, asyncio.Lock())


async def persist_portrait_bytes(user_id: int, data: bytes, content_type: str, *, directory: str = "") -> str:
    """写入用户资产目录的永久立绘并返回裸路径。"""
    ext = _UPLOAD_EXTS.get(content_type.split(";", maxsplit=1)[0].strip().lower(), "jpg")
    return await save_companion_asset_async(data, user_id=user_id, label="portrait", ext=ext, directory=directory)


async def persist_portrait_or_draft(data: bytes, user_id: int, content_type: str, *, persist: bool) -> str:
    """按确认状态保存永久立绘或临时草稿，返回裸路径。"""
    if persist:
        return await persist_portrait_bytes(user_id, data, content_type)
    src_content_type = content_type.split(";", maxsplit=1)[0].strip().lower()
    final_ext = _UPLOAD_EXTS.get(src_content_type, "jpg")
    file_id: str | None = None

    def save_draft() -> str:
        nonlocal file_id
        file_id = save_file(data, src_content_type, final_ext, user_id=user_id)
        return f"temp-media/{file_id}"

    try:
        return await run_blocking(save_draft)
    except asyncio.CancelledError:
        if file_id is not None:
            delete_portrait_file(f"temp-media/{file_id}")
        raise


def validate_transparent_portrait(data: bytes, *, require_clean_edges: bool = True) -> None:
    """验收已准备的透明 PNG，不修改图片。"""
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format != "PNG" or getattr(image, "is_animated", False):
                raise ActionMaterialRejectedError("全身图片须为静态透明 PNG，请重新生成或上传")
            image.load()
            validate_transparent_image(image.convert("RGBA"), require_clean_edges=require_clean_edges)
    except (OSError, ValueError) as exc:
        raise ActionMaterialRejectedError("全身图片无法读取，请重新生成或上传", internal=str(exc)) from exc


async def _generate_portrait(
    prompt: str,
    user_id: int,
    *,
    task: AiTaskKey,
    persist: bool,
    request_context: str,
    reference_image: str | None = None,
    secondary_reference_image: str | None = None,
    size: str = _AVATAR_SIZE,
    image_edit: bool = False,
    prefer_transparent_background: bool = False,
    selection: ProviderSelection | None = None,
) -> tuple[str, GenerationOrigin, int]:
    try:
        result = await generate_appearance_image(
            user_id,
            prompt,
            request_context=request_context,
            size=size,
            reference_image=reference_image,
            secondary_reference_image=secondary_reference_image,
            image_edit=image_edit,
            prefer_transparent_background=prefer_transparent_background,
            selection=selection,
            task=task,
        )
        path = result.path
        if not persist:
            data, content_type = await image_asset_bytes(path)
            path = await persist_portrait_or_draft(data, user_id, content_type, persist=False)
        return path, result.origin, result.job_id
    except ImageGenerationError as exc:
        logger.warning("portrait image generation failed", extra={"user_id": user_id, "error": exc.internal})
        raise AvatarGenerationError(str(exc), internal=exc.internal) from exc


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
    origin: GenerationOrigin | None = None,
    generation_job_id: int | None = None,
) -> AvatarAsset:
    """以新头像行替换激活头像并撤销头像确认；正式原图继续由历史行持有。"""
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
                .execution_options(synchronize_session=False)
            )
            asset = AvatarAsset(
                user_id=user_id,
                prompt_json=prompt_payload,
                asset_url=asset_url,
                generation_origin_json=origin.model_dump(mode="json") if origin else None,
                active=True,
            )
            await reset_portrait_confirmation(db, user_id)
            db.add(asset)
            await enqueue_asset_cleanup(db, user_id, [previous_url] if previous_url else [])
            await complete_appearance_image(db, user_id, generation_job_id)
            await commit_appearance(db, user_id)
        except BaseException:
            await db.rollback()
            if generation_job_id is None or asset_url.startswith("temp-media/"):
                delete_portrait_file(asset_url)
            raise

    if persist and previous_url is not None and previous_url.startswith("temp-media/"):
        delete_portrait_file(previous_url)
    await cleanup_user_assets(user_id)
    return asset


async def _generate_avatar_step(
    user_id: int,
    *,
    avatar_prompt: str,
    persist: bool,
    feedback: str | None = None,
    request_context: str | None = None,
    reference_image: str | None = None,
    secondary_reference_image: str | None = None,
    image_edit: bool = False,
    selection: ProviderSelection | None = None,
) -> AvatarAsset:
    """先在会话外完成立绘生成，再用一次短写会话提交新的 active AvatarAsset 行。"""
    asset_url, origin, generation_job_id = await _generate_portrait(
        avatar_prompt,
        user_id,
        persist=persist,
        request_context=request_context if request_context is not None else feedback or "",
        reference_image=reference_image,
        secondary_reference_image=secondary_reference_image,
        image_edit=image_edit,
        task=AiTaskKey.appearance,
        selection=selection,
    )
    return await _write_avatar_step(
        user_id,
        asset_url=asset_url,
        origin=origin,
        generation_job_id=generation_job_id,
        avatar_prompt=avatar_prompt,
        style=_DEFAULT_STYLE,
        persist=persist,
        feedback=feedback,
        reference_image=reference_image,
        secondary_reference_image=secondary_reference_image,
    )


def read_portrait_bytes(path: str | None) -> tuple[bytes, str] | None:
    """读取立绘裸路径的字节与类型；文件缺失、过期或不可读时返回 None。"""
    resolved = resolve_asset_reference(path)
    if resolved is None:
        return None
    try:
        return resolved[0].read_bytes(), resolved[1]
    except OSError:
        return None


def delete_portrait_file(path: str | None) -> None:
    """尽力删除立绘文件：temp-media 草稿或 companion-assets 用户资产。"""
    if path and path.startswith("companion-assets/"):
        unlink_companion_asset(path)
        return
    resolved = resolve_asset_reference(path)
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


async def _require_complete_persona(db: AsyncSession, user_id: int) -> PersonaSnapshot:
    """重读 persona（缺则建）并要求引导已完成。"""
    persona = await ensure_persona_snapshot(db, user_id)
    if not persona.is_complete:
        raise PersonaIncompleteError("请先完成引导再生成形象", internal="persona is incomplete")
    return persona


async def _verified_persona(user_id: int) -> PersonaSnapshot:
    """头像入口共用的前置校验：onboarding 完成且形象未锁定。"""
    async with SESSION_LOCAL() as db:
        persona = await _require_complete_persona(db, user_id)
        await raise_if_image_sealed(db, user_id, persona)
    return persona


async def _verified_persona_with_asset(user_id: int) -> tuple[PersonaSnapshot, AvatarAsset | None]:
    """_verified_persona 的带 asset 变体：单次读取激活头像供调用方复用，锁外读到的快照可能已过期，不接收。"""
    async with SESSION_LOCAL() as db:
        persona = await _require_complete_persona(db, user_id)
        current = await get_active_avatar(db, user_id)
        _raise_if_sealed(persona, current)
    return persona, current


async def _avatar_description(
    user_id: int, persona: PersonaSnapshot, feedback: str | None, *, has_reference: bool = False
) -> str:
    try:
        return await enhance_avatar_prompt(user_id, persona, feedback=feedback, has_reference=has_reference)
    except (ValidationError, RuntimeError, LLMRuntimeError) as exc:
        raise AvatarGenerationError("形象描述生成失败，请稍后重试", internal=str(exc)) from exc


async def generate_avatar(user_id: int, *, feedback: str | None = None) -> AvatarAsset:
    """按角色资料与本次要求生成头像。"""
    async with get_avatar_job_lock(user_id):
        persona = await _verified_persona(user_id)
        return await _generate_avatar(user_id, feedback=feedback, persona=persona)


async def _generate_avatar(
    user_id: int, *, persona: PersonaSnapshot, feedback: str | None = None, selection: ProviderSelection | None = None
) -> AvatarAsset:
    return await _generate_avatar_step(
        user_id,
        selection=selection,
        avatar_prompt=await _avatar_description(user_id, persona, feedback),
        request_context=json.dumps(
            [persona.definition_json, persona.personality_tags_json, feedback], ensure_ascii=False, sort_keys=True
        ),
        persist=persona.is_portrait_confirmed,
        feedback=feedback,
    )


async def get_active_avatar(db: AsyncSession, user_id: int) -> AvatarAsset | None:
    return await db.scalar(select(AvatarAsset).where(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)))


async def select_avatar(db: AsyncSession, user_id: int, avatar_id: int) -> AvatarAsset:
    """将指定头像设为激活态，并取消该用户其余头像的激活；换用另一张头像时撤销头像确认。"""
    # 切换头像会改变已确认身份，同样受身份锁保护。
    persona = await ensure_persona_snapshot(db, user_id)
    await raise_if_image_sealed(db, user_id, persona)
    asset = await db.scalar(select(AvatarAsset).where(AvatarAsset.id == avatar_id, AvatarAsset.user_id == user_id))
    if asset is None:
        raise AvatarNotFoundError("找不到对应的形象", internal=f"avatar {avatar_id} not found")
    if not asset.active:
        # 与生成新头像一致：确认只对当时的头像有效，换用的头像须重新确认后才能锁定身份。
        await reset_portrait_confirmation(db, user_id)
    await db.execute(
        update(AvatarAsset).where(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)).values(active=False)
    )
    asset.active = True
    await commit_appearance(db, user_id)
    return asset


async def list_avatar_history(db: AsyncSession, user_id: int, limit: int = 20) -> list[AvatarAsset]:
    assets = (
        await db.scalars(
            select(AvatarAsset)
            .where(AvatarAsset.user_id == user_id)
            .order_by(AvatarAsset.created_at.desc())
            .limit(limit)
        )
    ).all()
    survivors: list[AvatarAsset] = []
    for asset in assets:
        if _is_orphan_temp_media_asset(asset):
            await db.delete(asset)
            continue
        survivors.append(asset)
    if len(survivors) != len(assets):
        await commit_appearance(db, user_id)
    return survivors


def _is_orphan_temp_media_asset(asset: AvatarAsset) -> bool:
    """头像草稿所有图片字段都指向已过期 temp-media → DB 行无可用图片，按孤儿清理。字段全空或任一字段仍有活图则不视作孤儿。"""
    paths = [path for path in (asset.asset_url, asset.seed_fullbody_url) if path]
    return bool(paths) and all(
        path.startswith("temp-media/") and resolve_asset_reference(path) is None for path in paths
    )


async def regenerate_avatar(mode: ImageReviseMode, user_id: int, feedback: str | None = None) -> AvatarAsset:
    """根据反馈微调当前头像，或按角色资料重新生成。"""
    async with get_avatar_job_lock(user_id):
        persona, current = await _verified_persona_with_asset(user_id)
        origin = (
            GenerationOrigin.model_validate(current.generation_origin_json)
            if current is not None and current.generation_origin_json
            else None
        )
        selection = ProviderSelection(origin=origin, mode="current" if mode == "edit" else "next")
        if mode == "regenerate":
            return await _generate_avatar(user_id, feedback=feedback, selection=selection, persona=persona)
        effective_feedback = (feedback or "").strip()
        if not effective_feedback:
            raise AvatarGenerationError("请先描述要微调的内容")
        # 编辑底图即当前激活头像，成功后照常写入新 AvatarAsset 行。
        if current is None:
            raise AvatarNotFoundError("找不到当前头像，请重新生成")
        edit_uri = await asyncio.to_thread(read_asset_data_uri, current.asset_url)
        if not edit_uri:
            raise AvatarSourceUnreadableError("当前头像文件缺失或无法读取，请重新生成")
        return await _generate_avatar_step(
            user_id,
            avatar_prompt=build_image_edit_prompt(effective_feedback, preserve=EDIT_PRESERVE_AVATAR),
            persist=persona.is_portrait_confirmed,
            feedback=effective_feedback,
            reference_image=edit_uri,
            image_edit=True,
            selection=selection,
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
        persona, current = await _verified_persona_with_asset(user_id)
        origin = (
            GenerationOrigin.model_validate(current.generation_origin_json)
            if current is not None and current.generation_origin_json
            else None
        )
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
            request_context=json.dumps(
                [persona.definition_json, persona.personality_tags_json, description],
                ensure_ascii=False,
                sort_keys=True,
            ),
            selection=ProviderSelection(origin=origin, mode="next"),
            persist=persona.is_portrait_confirmed,
            feedback=description,
            reference_image=await asyncio.to_thread(build_data_uri, data, content_type),
            secondary_reference_image=secondary_uri,
        )


async def finalize_avatar(db: AsyncSession, user_id: int, *, expected_avatar_id: int) -> AvatarAsset | None:
    """确认头像；全身草稿只由全身确认入口转正。激活头像须仍是预览的那一条，否则零副作用拒绝。"""
    asset = await get_active_avatar(db, user_id)
    if asset is None:
        return None
    if asset.id != expected_avatar_id:
        raise AvatarConflictError("当前头像已更新，请重新加载预览后确认")

    if asset.asset_url.startswith("temp-media/"):
        draft = await asyncio.to_thread(read_portrait_bytes, asset.asset_url)
        if draft is None:
            raise AvatarSourceUnreadableError("头像草稿已过期或无法读取，请重新生成")
        new_path = await persist_portrait_bytes(user_id, *draft)
        try:
            asset.asset_url = new_path
            await commit_appearance(db, user_id)
        except BaseException:
            await db.rollback()
            delete_portrait_file(new_path)
            raise
    elif await asyncio.to_thread(resolve_asset_reference, asset.asset_url) is None:
        raise AvatarSourceUnreadableError("头像文件缺失或无法读取，请重新生成")
    return asset


async def _fetch_fullbody_target(db: AsyncSession, user_id: int, avatar_id: int) -> tuple[AvatarAsset, PersonaSnapshot]:
    """按 id 取回属于该用户的头像行与 persona。"""
    asset = await db.scalar(select(AvatarAsset).where(AvatarAsset.id == avatar_id, AvatarAsset.user_id == user_id))
    if asset is None:
        raise AvatarNotFoundError("找不到对应的形象", internal=f"avatar {avatar_id} not found")
    return asset, await ensure_persona_snapshot(db, user_id)


async def _resolve_fullbody_identity(db: AsyncSession, asset: AvatarAsset) -> VisualIdentitySnapshot | None:
    """目标须为当前激活头像；已确认身份时冻结当前视觉身份快照。"""
    if not asset.active:
        raise AvatarNotFoundError("请先选择当前角色的头像")
    return await require_visual_identity(db, asset.user_id) if asset.is_fullbody_confirmed else None


async def _load_fullbody_target(
    user_id: int, avatar_id: int
) -> tuple[AvatarAsset, PersonaSnapshot, VisualIdentitySnapshot | None]:
    """取回当前激活的目标头像与 persona；已确认身份时同时冻结当前视觉身份快照。"""
    async with SESSION_LOCAL() as db:
        asset, persona = await _fetch_fullbody_target(db, user_id, avatar_id)
        identity = await _resolve_fullbody_identity(db, asset)
    return asset, persona, identity


async def _prepare_fullbody_reference(
    user_id: int,
    asset: AvatarAsset,
    persona: PersonaSnapshot,
    identity: VisualIdentitySnapshot | None,
    *,
    feedback: str | None,
    secondary_reference: str | None = None,
    canvas_aspect: str | None = None,
    require_transparent_background: bool = False,
) -> tuple[str, str]:
    """返回头像参考与完整提示词，供内部生成和外部制作共用。"""
    reference = await asyncio.to_thread(read_asset_data_uri, asset.seed_fullbody_url if identity else asset.asset_url)
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
                        CompanionOutfit.is_initial.is_(True),
                        CompanionOutfit.status == "ready",
                    )
                )
                or ""
            )
    personality = str(definition.get("personality") or "").strip()
    direction = await describe_character_form(
        user_id,
        species=species,
        personality=personality,
        feedback=feedback or "",
        outfit_description=outfit_description,
        reference_images=(reference, secondary_reference) if secondary_reference else (reference,),
        allow_body_change=True,
    )
    prompt = build_fullbody_reference_prompt(
        body_direction=direction,
        species=species,
        gender=definition.get("gender", ""),
        personality=personality,
        feedback=feedback,
        has_user_reference=bool(secondary_reference),
        outfit_description=outfit_description,
        canvas_aspect=canvas_aspect,
        require_transparent_background=require_transparent_background,
    )
    return reference, prompt


async def _install_fullbody_seed(
    user_id: int,
    *,
    avatar_id: int,
    url: str,
    prompt: str | None,
    origin: GenerationOrigin | None = None,
    generation_job_id: int | None = None,
) -> AvatarAsset:
    """安装首次全身图；已确认原图的失败替换与任务重置同事务提交。调用方持用户锁。"""
    try:
        async with SESSION_LOCAL() as session:
            target = await session.scalar(
                select(AvatarAsset).where(
                    AvatarAsset.id == avatar_id, AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)
                )
            )
            if target is None:
                raise AvatarNotFoundError("当前角色已切换，请重新打开全身参考图")
            if target.is_fullbody_confirmed:
                raise AvatarGenerationError("角色资料正在准备或已更新，请重新加载全身形象")
            previous_url = target.seed_fullbody_url
            raw = target.prompt_json or {}
            payload = raw if isinstance(raw, dict) else {}
            if prompt is None:
                payload.pop("fullbody_reference_prompt", None)
            else:
                payload["fullbody_reference_prompt"] = prompt
            target.prompt_json = payload
            target.seed_fullbody_url = url
            target.fullbody_generation_origin_json = origin.model_dump(mode="json") if origin else None
            await enqueue_asset_cleanup(session, user_id, [previous_url] if previous_url else [])
            await complete_appearance_image(session, user_id, generation_job_id)
            await commit_appearance(session, user_id)
    except BaseException:
        if generation_job_id is None or url.startswith("temp-media/"):
            delete_portrait_file(url)
        raise
    if previous_url and previous_url != url and previous_url.startswith("temp-media/"):
        delete_portrait_file(previous_url)
    await cleanup_user_assets(user_id)
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
        try:
            await asyncio.to_thread(require_matting_model)
        except VideoProcessError as exc:
            raise FullbodyGenerationError(str(exc), internal=exc.internal or str(exc)) from exc
        effective_feedback = (feedback or "").strip()
        secondary_reference = None
        base_fullbody_url = asset.seed_fullbody_url
        origin_json = asset.fullbody_generation_origin_json
        edit_path = asset.seed_fullbody_url
        if candidate_id is not None:
            if identity is None:
                raise AvatarGenerationError("首次全身形象尚无候选图可调整")
            async with SESSION_LOCAL() as candidate_db:
                candidate = await candidate_db.scalar(
                    select(FullbodyCandidate).where(
                        FullbodyCandidate.id == candidate_id,
                        FullbodyCandidate.user_id == user_id,
                        FullbodyCandidate.avatar_id == avatar_id,
                    )
                )
            if (
                candidate is None
                or candidate.status not in ("ready", "failed")
                or candidate.base_revision != identity.revision
                or candidate.base_fullbody_url != base_fullbody_url
            ):
                raise AvatarGenerationError("全身候选图已变化，请刷新后重试")
            edit_path = candidate.image_url
            origin_json = candidate.generation_origin_json
        selection = ProviderSelection(
            origin=GenerationOrigin.model_validate(origin_json) if origin_json else None,
            mode="current" if mode == "edit" else "next",
        )
        if mode == "edit":
            if reference_image:
                raise AvatarGenerationError("参考图只用于重新生成，微调请先移除参考图")
            if not effective_feedback:
                raise AvatarGenerationError("请先描述要微调的内容")
            reference_uri = await asyncio.to_thread(read_asset_data_uri, edit_path)
            if not reference_uri:
                raise AvatarSourceUnreadableError("上一版全身参考缺失或无法读取，请先重新生成")
            prompt = build_image_edit_prompt(effective_feedback, preserve=EDIT_PRESERVE_FULLBODY)
        else:
            if reference_image:
                secondary_reference = await asyncio.to_thread(
                    build_data_uri, reference_image, reference_content_type or "image/png"
                )
            reference_uri, prompt = await _prepare_fullbody_reference(
                user_id, asset, persona, identity, feedback=effective_feedback, secondary_reference=secondary_reference
            )
        try:
            generated_url, origin, generation_job_id = await _generate_portrait(
                prompt,
                user_id,
                persist=asset.is_fullbody_confirmed,
                request_context=json.dumps(
                    [avatar_id, asset.identity_revision, base_fullbody_url, mode, effective_feedback, candidate_id]
                ),
                reference_image=reference_uri,
                secondary_reference_image=secondary_reference,
                size=FULLBODY_SIZE,
                image_edit=mode == "edit",
                prefer_transparent_background=True,
                task=AiTaskKey.appearance,
                selection=selection,
            )
        except AvatarGenerationError as exc:
            raise FullbodyGenerationError(str(exc), internal=exc.internal) from exc

        if identity is not None:
            return await _save_fullbody_candidate(
                user_id, avatar_id, generated_url, identity, base_fullbody_url, origin, generation_job_id
            )
        return await _install_fullbody_seed(
            user_id,
            avatar_id=avatar_id,
            url=generated_url,
            prompt=prompt,
            origin=origin,
            generation_job_id=generation_job_id,
        )


async def confirm_fullbody_seed(user_id: int, *, avatar_id: int, expected_url: str) -> AvatarAsset:
    """保存用户确认的原图并登记后台准备；确认后可继续引导。"""
    async with get_avatar_job_lock(user_id):
        async with SESSION_LOCAL() as db:
            asset, persona = await _fetch_fullbody_target(db, user_id, avatar_id)
            if not asset.active or not persona.is_portrait_confirmed:
                raise AvatarGenerationError("请先确认当前头像")
            if asset.is_fullbody_confirmed:
                return asset
            original_avatar_url = asset.asset_url
            original_seed_url = asset.seed_fullbody_url
        if (
            original_avatar_url.startswith("temp-media/")
            or await asyncio.to_thread(resolve_asset_reference, original_avatar_url) is None
        ):
            raise AvatarSourceUnreadableError("头像文件缺失或尚未确认，请重新确认头像")
        if not original_seed_url or normalize_asset_reference(expected_url) != original_seed_url:
            raise AvatarSourceUnreadableError("全身形象已变更，请重新加载后确认")
        seed = await asyncio.to_thread(read_portrait_bytes, original_seed_url)
        if seed is None:
            raise AvatarSourceUnreadableError("全身形象缺失或已过期，请重新生成")
        seed_path: str | None = None
        try:
            if original_seed_url.startswith("temp-media/"):
                seed_path = await persist_portrait_bytes(user_id, seed[0], seed[1])
            async with SESSION_LOCAL() as db:
                asset, persona = await _fetch_fullbody_target(db, user_id, avatar_id)
                await db.refresh(asset, with_for_update={"of": AvatarAsset})
                persona = await lock_persona_snapshot(db, user_id)
                if (
                    not asset.active
                    or not persona.is_portrait_confirmed
                    or asset.is_fullbody_confirmed
                    or asset.asset_url != original_avatar_url
                    or asset.seed_fullbody_url != original_seed_url
                ):
                    raise AvatarGenerationError("形象已变更，请重新加载后确认")
                asset.seed_fullbody_url = seed_path or original_seed_url
                asset.is_fullbody_confirmed = True
                await initialize_visual_identity(db, asset)
                await commit_appearance(db, user_id)
        except BaseException:
            delete_portrait_file(seed_path)
            raise
        return asset


async def prepare_initial_fullbody(user_id: int, *, preparation_id: str) -> bool:
    """确认原图保持不变；透明结果只写入默认造型。"""
    async with SESSION_LOCAL() as db:
        asset = await get_visual_identity_asset(db, user_id)
        if asset is None or asset.preparation_job is None or asset.preparation_job.preparation_id != preparation_id:
            return False
        job = asset.preparation_job
        if asset.identity_revision != job.identity_revision or asset.seed_fullbody_url != job.body_source_path:
            return False
        initial_outfit = await initial_outfit_snapshot(db, user_id)
        if initial_outfit is not None and initial_outfit.fullbody_url:
            return True
        source_path, avatar_id, revision = job.body_source_path, asset.id, asset.identity_revision
    seed = await run_blocking(read_portrait_bytes, source_path)
    if seed is None:
        raise AvatarSourceUnreadableError("全身形象原图缺失，请重新提供全身图片")
    try:
        prepared = await run_blocking(prepare_transparent_image, seed[0], require_clean_edges=False)
    except VideoProcessError as exc:
        raise AvatarGenerationError(str(exc), internal=exc.internal or str(exc)) from exc
    outfit_path: str | None = None
    try:
        async with get_avatar_job_lock(user_id), SESSION_LOCAL() as db:
            asset = await get_visual_identity_asset(db, user_id, lock=True)
            if (
                asset is None
                or asset.id != avatar_id
                or asset.identity_revision != revision
                or asset.preparation_job is None
                or asset.preparation_job.preparation_id != preparation_id
            ):
                return False
            outfit = await db.scalar(
                select(CompanionOutfit).where(CompanionOutfit.user_id == user_id, CompanionOutfit.is_initial.is_(True))
            )
            if outfit is not None and outfit.fullbody_url:
                return True
            if outfit is None:
                outfit = CompanionOutfit(
                    user_id=user_id,
                    name="默认外观",
                    fullbody_url="",
                    status="ready",
                    is_initial=True,
                    source_json=OutfitSource(identity_reference_path=source_path, identity_revision=revision).dump(),
                )
                db.add(outfit)
                await flush_appearance(db, user_id)
            outfit_path = await persist_portrait_bytes(
                user_id, prepared, "image/png", directory=outfit_asset_directory(outfit.id)
            )
            outfit.fullbody_url = outfit_path
            outfit.description_status = "pending"
            emit_ws_event(db, user_id=user_id, event_type="companion.outfit.updated", payload={"outfit_id": outfit.id})
            await commit_appearance(db, user_id)
    except BaseException:
        delete_portrait_file(outfit_path)
        raise
    await cleanup_user_assets(user_id)
    return True


async def prepare_fullbody_prompt(user_id: int, *, avatar_id: int, feedback: str | None = None) -> str:
    """自备图与 AI 使用相同的视觉判断和全身模板。"""
    asset, persona, identity = await _load_fullbody_target(user_id, avatar_id)
    _, prompt = await _prepare_fullbody_reference(
        user_id,
        asset,
        persona,
        identity,
        feedback=feedback,
        canvas_aspect=FULLBODY_ASPECT,
        require_transparent_background=True,
    )
    return prompt


async def adopt_fullbody_seed(
    user_id: int, *, avatar_id: int, data: bytes, content_type: str
) -> AvatarAsset | FullbodyCandidateResponse:
    """安装用户确认的全身图；未确认身份时保留草稿，已确认时不改变既有外观。"""
    if not data:
        raise ValueError("image data is required")
    async with get_avatar_job_lock(user_id):
        async with SESSION_LOCAL() as db:
            asset, _ = await _fetch_fullbody_target(db, user_id, avatar_id)
            identity = await _resolve_fullbody_identity(db, asset)
        if identity is None:
            url = await persist_portrait_or_draft(data, user_id, content_type, persist=asset.is_fullbody_confirmed)
            return await _install_fullbody_seed(user_id, avatar_id=avatar_id, url=url, prompt=None)
        try:
            data = await asyncio.to_thread(prepare_transparent_image, data, require_clean_edges=False)
        except VideoProcessError as exc:
            raise AvatarGenerationError(str(exc), internal=exc.internal or str(exc)) from exc
        url = await persist_portrait_or_draft(data, user_id, "image/png", persist=asset.is_fullbody_confirmed)
        return await _save_fullbody_candidate(user_id, avatar_id, url, identity, asset.seed_fullbody_url)


async def adopt_avatar_seed(user_id: int, *, data: bytes, content_type: str) -> AvatarAsset:
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
    return build_avatar_reference_prompt(personality="", description=prompt, feedback=feedback)


async def retry_initial_preparation(
    db: AsyncSession, user_id: int, avatar_id: int, expected_revision: int
) -> AvatarAsset:
    asset = await get_visual_identity_asset(db, user_id, lock=True)
    if (
        asset is None
        or asset.id != avatar_id
        or asset.identity_revision != expected_revision
        or asset.preparation_job is None
    ):
        raise AvatarConflictError("确认形象已更新，请刷新后重试")
    if asset.preparation_job.state in ("failed", "paused", "unknown"):
        asset.preparation_job.state, asset.preparation_job.error = "queued", None
        emit_preparation_updated(db, user_id, avatar_id)
        await commit_appearance(db, user_id)
    return asset


async def fullbody_review_view(user_id: int, job_id: int) -> MediaJobView:
    async with SESSION_LOCAL() as db:
        job = await db.scalar(
            select(FullbodyReviewJob).where(FullbodyReviewJob.id == job_id, FullbodyReviewJob.user_id == user_id)
        )
        if job is None:
            raise RequestError(status_code=404, message="找不到全身检查任务")
        candidate = await db.get(FullbodyCandidate, job.candidate_id)
        report_data = candidate.review_json if candidate else {}
        report = IdentityReview.model_validate(report_data["report"]) if report_data.get("report") else None
        candidate_key = candidate_review_key(job.source_key)
        review = (
            VisualReview(
                required=("identity",),
                identity=ReviewDecision(verdict=report.verdict, reason=report.reason),
                status=report.status,
                evidence=report.evidence,
            )
            if report
            else None
        )
        if report_data.get("manual_review"):
            review = VisualReview.model_validate(report_data["manual_review"])
        basis = review_basis(review, None, candidate_key)
        return MediaJobView(
            id=job.id,
            kind=job.kind,
            state=job.state,
            stage=job.stage,
            revision=job.revision,
            required_slots=1,
            ready_slots=int(review is not None and review.verdict == "pass"),
            complete=job.state == "succeeded",
            slots=[
                MediaSlotView(
                    slot=0,
                    media_id=str(job.candidate_id),
                    status=job.stage,
                    review=review,
                    basis_id=basis,
                    candidate_basis_id=candidate_key,
                    preview_url=re_sign_bare_path(
                        candidate.image_url if candidate else (job.checkpoint or {}).get("candidate_path")
                    ),
                    can_review=job.stage == "review_pending"
                    and candidate is not None
                    and (report is None or report.verdict == "review" and report.status != "blocked"),
                    error=candidate.error if candidate else None,
                    candidates=await manual_candidate_views(
                        [MediaCandidate(path=candidate.image_url, attempt=0, review=review)]
                    )
                    if candidate
                    else [],
                )
            ],
        )


async def recover_fullbody_reviews() -> None:
    async with SESSION_LOCAL() as db:
        jobs = await db.scalars(select(FullbodyReviewJob).where(FullbodyReviewJob.state == "running"))
        for job in jobs:
            job.state, job.stage = "paused", "review_pending"
            job.error = "图片检查已中断，候选和原依据已保留"
            candidate = await db.get(FullbodyCandidate, job.candidate_id) if job.candidate_id else None
            if candidate is not None:
                candidate.review_json = {"job_id": job.id, "report": (job.checkpoint or {}).get("report")}
                candidate.status, candidate.error = "pending", job.error
                await flush_appearance(db, job.user_id)
        await db.commit()


async def review_fullbody_job(user_id: int, job_id: int, slot: int, expected: MediaReviewRequest) -> MediaJobView:
    async with SESSION_LOCAL() as db:
        job = await db.scalar(
            select(FullbodyReviewJob).where(FullbodyReviewJob.id == job_id, FullbodyReviewJob.user_id == user_id)
        )
        if job is None or slot != 0:
            raise RequestError(status_code=404, message="找不到全身检查任务")
        candidate = await db.get(FullbodyCandidate, job.candidate_id)
        if candidate is None:
            raise RequestError(status_code=404, message="全身候选已不存在")
    await _review_fullbody_candidate(user_id, candidate.id, candidate.image_url, expected)
    return await fullbody_review_view(user_id, job_id)
