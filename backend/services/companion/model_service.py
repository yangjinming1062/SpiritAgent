"""3D 模型 controller：建行 + 调度 in-process 管道。所有 download / poll / SPEC 校验 / 落库 都在 ``pipeline`` 内完成。"""

from components import get_logger
from modules.companion import AvatarAsset, Companion3DModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from services.llm import chat, is_preset_species, resolve_fullbody_style

from .persona_service import get_or_create_persona, load_persona_definition
from .pipeline import (
    IN_FLIGHT_STATUSES,
    RETRYABLE_DOWNLOAD_STATUSES,
    ModelGenerationError,
    ModelGenerationInProgressError,
    _launch_pipeline_task,
    _probe_paid_failure,
    _raw_provider_name,
    _resolve_model_provider,
    _ResumeOutcome,
    get_active_model,
    get_model_job_lock,
)
from .rig_type_selector import classify_species

logger = get_logger(__name__)


async def _resolve_active_avatar(db: AsyncSession, user_id: int) -> AvatarAsset:
    avatar = (
        await db.execute(select(AvatarAsset).where(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)))
    ).scalar_one_or_none()
    if avatar is None or not (avatar.seed_front_2d_url or avatar.asset_url):
        raise ModelGenerationError("没有找到形象头像，请先完成引导流程中的形象生成")
    return avatar


def _avatar_view_filenames(avatar: AvatarAsset) -> dict[str, str]:
    """把 avatar 上的种子图 URL 解析成 view_filenames，供 pipeline 读盘用。"""

    def _name(url: str) -> str:
        return url.rsplit("/", maxsplit=1)[-1].split("?", maxsplit=1)[0]

    front = _name(avatar.seed_front_3d_url or avatar.seed_front_2d_url or avatar.asset_url)
    if not front:
        raise ModelGenerationError("请先生成正面全身图再生成模型")
    out: dict[str, str] = {"front": front}
    if avatar.seed_back_url and (back := _name(avatar.seed_back_url)):
        out["back"] = back
    return out


async def _avatar_style(db: AsyncSession, avatar: AvatarAsset, species: str) -> str:
    """3D 模型风格路由：类人物种走 CG 风格（anime_game_cg），非人物种走写实风格（realistic）。"""
    has_humanoid_face = None
    if not is_preset_species(species):
        has_humanoid_face = (await classify_species(chat, species, db=db, user_id=avatar.user_id))[1]
    return resolve_fullbody_style(species, has_humanoid_face)


async def generate_companion_model(
    db: AsyncSession,
    *,
    user_id: int,
    species_override: str | None = None,
    provider_override: str | None = None,
    force: bool = False,
) -> Companion3DModel:
    """生成 3D 模型：已有生效且成功的模型在非 force 时复用；新请求创建新记录行并将旧记录置为非激活。"""
    persona = await get_or_create_persona(db, user_id)
    definition = load_persona_definition(persona)
    species = species_override or definition.get("biological_type", "人类")

    async with get_model_job_lock(user_id):
        in_flight = (
            await db.execute(
                select(Companion3DModel)
                .where(Companion3DModel.user_id == user_id, Companion3DModel.status.in_(IN_FLIGHT_STATUSES))
                .limit(1),
            )
        ).scalar_one_or_none()
        if in_flight is not None:
            raise ModelGenerationInProgressError("已有 3D 模型生成任务进行中，请稍候再试")

        if not force:
            existing = await get_active_model(db, user_id)
            if existing is not None and existing.status == "succeeded" and existing.asset_url:
                logger.info(
                    "Companion model already exists; skipping generation",
                    extra={"user_id": user_id, "model_id": existing.id},
                )
                return existing
            retryable = (
                await db.execute(
                    select(Companion3DModel)
                    .where(
                        Companion3DModel.user_id == user_id,
                        Companion3DModel.status == "download_failed",
                        Companion3DModel.provider_task_id.isnot(None),
                    )
                    .order_by(Companion3DModel.id.desc())
                    .limit(1),
                )
            ).scalar_one_or_none()
            if retryable is not None:
                logger.info(
                    "Companion model awaits download retry; skipping generation",
                    extra={"user_id": user_id, "model_id": retryable.id},
                )
                return retryable
            # 行持有任务 id 时先查询供应商真实状态：成功则接续下载，确认失败才允许重新提交。
            failed_with_task = (
                await db.execute(
                    select(Companion3DModel)
                    .where(
                        Companion3DModel.user_id == user_id,
                        Companion3DModel.status == "failed",
                        Companion3DModel.provider_task_id.isnot(None),
                    )
                    .order_by(Companion3DModel.id.desc())
                    .limit(1),
                )
            ).scalar_one_or_none()
            if failed_with_task is not None:
                outcome = await _probe_paid_failure(failed_with_task)
                if outcome is _ResumeOutcome.RESUMED:
                    await db.refresh(failed_with_task)
                    logger.info(
                        "Resumed locally-failed row whose tripo task was actually success",
                        extra={
                            "user_id": user_id,
                            "model_id": failed_with_task.id,
                            "task_id": failed_with_task.provider_task_id,
                        },
                    )
                    return failed_with_task
                if outcome is _ResumeOutcome.UNKNOWN:
                    logger.info(
                        "Probe tripo uncertain; leaving failed row untouched",
                        extra={
                            "user_id": user_id,
                            "model_id": failed_with_task.id,
                            "task_id": failed_with_task.provider_task_id,
                        },
                    )
                    return failed_with_task

        provider = _resolve_model_provider(provider_override)
        avatar = await _resolve_active_avatar(db, user_id)
        view_filenames = _avatar_view_filenames(avatar)
        selected_style = await _avatar_style(db, avatar, species)

        await db.execute(
            update(Companion3DModel)
            .where(Companion3DModel.user_id == user_id, Companion3DModel.active.is_(True))
            .values(active=False),
        )

        model = Companion3DModel(
            user_id=user_id,
            status="generating",
            species=species,
            style=selected_style,
            active=False,
        )
        db.add(model)
        await db.commit()
        await db.refresh(model)

    _launch_pipeline_task(
        model_id=model.id,
        user_id=user_id,
        provider_name=provider.provider_name,
        view_filenames=view_filenames,
        species=species,
        style=selected_style,
    )
    logger.info(
        "image-to-3d model generation dispatched in-process",
        extra={"user_id": user_id, "species": species, "provider": provider.provider_name},
    )
    return model


async def request_model_download_retry(db: AsyncSession, *, user_id: int, model_id: int) -> Companion3DModel:
    """把重试交给 ``pipeline._launch_pipeline_task`` 自驱续跑，不重新计费（docs/PROTOCOL.md §1.2）。"""
    model = (
        await db.execute(
            select(Companion3DModel).where(Companion3DModel.id == model_id, Companion3DModel.user_id == user_id),
        )
    ).scalar_one_or_none()
    if model is None:
        raise ModelGenerationError("未找到对应的 3D 模型记录")
    if model.status not in RETRYABLE_DOWNLOAD_STATUSES:
        raise ModelGenerationError("当前模型状态不支持重试下载")
    if not model.provider_task_id:
        raise ModelGenerationError("该行缺少 task_id，无法重试下载；请重新生成")
    _launch_pipeline_task(
        model_id=model_id,
        user_id=user_id,
        provider_name=_raw_provider_name(model.provider),
        view_filenames={},
        species=model.species or "人类",
        style=model.style or "realistic",
    )
    logger.info(
        "model download retry dispatched to in-process pipeline",
        extra={
            "user_id": user_id,
            "model_id": model_id,
            "task_id": model.provider_task_id,
            "phase": model.provider_phase,
        },
    )
    return model
