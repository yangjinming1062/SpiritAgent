"""动作制作：references。"""

import contextlib
import json

from infrastructure.ai.error_classifier import LlmCallBlockedError
from infrastructure.ai.llm_client import build_provider, resolve_provider_chain
from infrastructure.ai.providers.base import VideoGenProvider
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.database import SESSION_LOCAL
from infrastructure.video_processing.ffmpeg import VideoProcessError
from infrastructure.video_processing.matting import require_matting_model
from runtime.paid_work import GenerationWorkPaused, require_new_generation_call
from runtime.user_maintenance import is_user_in_maintenance
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.actions.asset_retirement import retire_action_assets
from features.actions.generation.state import GenerationContext
from features.actions.materials import accepted_action_asset
from features.actions.models import CompanionAction, CompanionActionPack
from features.actions.schemas import ABSOLUTE_MAX_DURATION_SECONDS
from features.appearance.avatar_service import load_avatar_bytes_as_data_uri, read_portrait_bytes
from features.appearance.outfit_service import wait_outfit_description
from features.appearance.queries import avatar_snapshot, outfit_snapshot
from features.appearance.query_contracts import AvatarSnapshot
from features.appearance.visual_identity import align_character_reference
from features.media.chain import FrozenMediaProvider, MediaChainState
from features.media.character_images import ImageChainState

from .errors import VideoPackError, VideoPackStateError, _GenerationRowGoneError
from .helpers import _artifact_abs_path, _image_data_uri, _pack_dir, _process_thread
from .queries import _active_avatar, _get_pack, _job_result_unresolved, _load_generation_context


async def _require_new_paid_step(pack: CompanionActionPack, context: GenerationContext) -> None:
    require_new_generation_call(pack.user_id)
    async with SESSION_LOCAL() as db:
        avatar = await _active_avatar(db, pack.user_id)
    try:
        await _require_current_identity_image(context, avatar)
    except VideoPackError as exc:
        raise LlmCallBlockedError(str(exc)) from exc
    require_new_generation_call(pack.user_id)


async def require_pack_generation_identity(db: AsyncSession, user_id: int, pack_id: int) -> None:
    pack = await _get_pack(db, user_id, pack_id)
    context = _load_generation_context(pack) if pack is not None else None
    if pack is None or context is None or not pack.reference_path:
        raise VideoPackStateError("该动作包没有可制作的冻结参考，请重新生成动作包")
    await _require_current_identity_image(context, await _active_avatar(db, user_id))


def require_action_matting_model() -> None:
    """动作素材都要经过抠像；模型缺失时在任何付费生成前拒绝。"""
    try:
        require_matting_model()
    except VideoProcessError as exc:
        raise VideoPackStateError(str(exc)) from exc


async def _reject_concurrent_build(db: AsyncSession, user_id: int, outfit_id: int) -> None:
    """同一外观同时只允许一个构建中的动作包，避免发布与激活竞态；不同外观可并行制作。"""
    processing = (
        await db.execute(
            select(CompanionActionPack.id).where(
                CompanionActionPack.user_id == user_id,
                CompanionActionPack.outfit_id == outfit_id,
                CompanionActionPack.status == "processing",
            ),
        )
    ).scalar_one_or_none()
    if processing is not None:
        raise VideoPackStateError("该外观已有动作包任务进行中，请等待完成后再试")


async def _reject_pack_regeneration(db: AsyncSession, user_id: int, outfit_id: int) -> None:
    jobs = (
        await db.scalars(
            select(CompanionAction)
            .join(CompanionActionPack, CompanionAction.pack_id == CompanionActionPack.id)
            .where(CompanionActionPack.user_id == user_id, CompanionActionPack.outfit_id == outfit_id),
        )
    ).all()
    if any(accepted_action_asset(job) is not None for job in jobs):
        raise VideoPackStateError("该着装已有就绪动作，请按需重新生成单个动作")
    if any(_job_result_unresolved(job) for job in jobs):
        raise VideoPackStateError("该着装仍有动作等待结果，请等待或核对原任务")


def _action_resolution(
    provider: VideoGenProvider,
    *,
    needs_loop_frames: bool,
    duration_seconds: float,
) -> str | None:
    if not provider.supports_first_frame:
        return None
    if needs_loop_frames and not provider.supports_loop_frames:
        return None
    seconds = int(round(duration_seconds))
    if provider.durations is not None and seconds not in provider.durations:
        return None
    return provider.max_resolution(
        duration=seconds,
        first_frame=True,
        last_frame=needs_loop_frames,
        reference_images=False,
    )


async def _resolve_action_video_plan(
    user_id: int,
    *,
    needs_loop_frames: bool,
    duration_seconds: float,
) -> tuple[float, list[FrozenMediaProvider]]:
    """按链序选择不短于设计的最小时长，再冻结支持该时长的后续供应商。"""
    if (
        not 1 <= duration_seconds <= ABSOLUTE_MAX_DURATION_SECONDS
        or abs(duration_seconds - round(duration_seconds)) > 1e-6
    ):
        raise VideoPackStateError("动作时长须为 1–15 的整秒数")
    requested = int(round(duration_seconds))
    async with SESSION_LOCAL() as db:
        chain = await resolve_provider_chain(db, user_id, "video_gen", task=AiTaskKey.window_action)
    configured = [(config, build_provider(config, VideoGenProvider)) for config in chain]
    for index, (_config, provider) in enumerate(configured):
        available = provider.durations if provider.durations is not None else (requested,)
        seconds = min(
            (
                value
                for value in available
                if requested <= value <= ABSOLUTE_MAX_DURATION_SECONDS
                and _action_resolution(provider, needs_loop_frames=needs_loop_frames, duration_seconds=value)
                is not None
            ),
            default=None,
        )
        if seconds is None:
            continue
        providers: list[FrozenMediaProvider] = []
        for config, current in configured[index:]:
            resolution = _action_resolution(current, needs_loop_frames=needs_loop_frames, duration_seconds=seconds)
            if resolution is not None:
                frozen = FrozenMediaProvider.from_config(config)
                frozen.video_resolution = resolution
                providers.append(frozen)
        return float(seconds), providers
    capability = "首尾帧" if needs_loop_frames else "首帧"
    raise VideoPackStateError(f"请配置支持{capability}、声明最高可用分辨率及 {requested}–15 秒内可用时长的视频模型")


def _freeze_action_video_plan(
    job: CompanionAction,
    duration_seconds: float,
    providers: list[FrozenMediaProvider],
) -> None:
    state = (
        MediaChainState.model_validate_json(job.generation_state_json)
        if job.generation_state_json
        else MediaChainState()
    )
    state.providers = providers
    job.target_duration_seconds = duration_seconds
    job.generation_state_json = state.model_dump_json()


async def _prepare_pack_identity(pack: CompanionActionPack, context: GenerationContext) -> GenerationContext:
    if context.reference_alignment == "ready":
        return context
    outfit_reference = await _process_thread(_image_data_uri, _artifact_abs_path(pack.reference_path))
    async with SESSION_LOCAL() as db:
        avatar = await avatar_snapshot(db, pack.user_id, context.identity.avatar_id)
    if avatar is None or not avatar.asset_url:
        raise VideoPackStateError("头像形象无法读取")
    portrait_source = await _process_thread(load_avatar_bytes_as_data_uri, avatar.asset_url)
    if not portrait_source:
        raise VideoPackStateError("头像形象无法读取")

    async def save_progress(state: ImageChainState) -> None:
        context.reference_chain = state
        context.reference_alignment = "running"
        async with SESSION_LOCAL() as db:
            row = await db.get(CompanionActionPack, pack.id)
            if row is None or row.status != "processing":
                raise _GenerationRowGoneError
            row.context_json = context.model_dump_json()
            await db.commit()

    path = await align_character_reference(
        pack.user_id,
        outfit_reference,
        context.identity,
        "",
        task=AiTaskKey.window_action,
        identity_reference=portrait_source,
        storage_directory=_pack_dir(pack),
        state=context.reference_chain,
        save_progress=save_progress,
        before_submit=lambda: _require_new_paid_step(pack, context),
    )
    previous_path = pack.reference_path
    async with SESSION_LOCAL() as db:
        row = await db.get(CompanionActionPack, pack.id)
        if row is None or row.status != "processing":
            raise VideoPackStateError("视频任务已失效")
        context.reference_alignment = "ready"
        row.reference_path = path
        row.context_json = context.model_dump_json()
        await retire_action_assets(db, pack.user_id, [previous_path])
        await db.commit()
    pack.reference_path = path
    pack.context_json = context.model_dump_json()
    return context


async def _freeze_outfit_description(pack: CompanionActionPack, context: GenerationContext) -> GenerationContext:
    if context.outfit_description_frozen:
        return context
    if is_user_in_maintenance(pack.user_id):
        raise GenerationWorkPaused
    if pack.outfit_id is not None:
        with contextlib.suppress(TimeoutError):
            await wait_outfit_description(pack.user_id, pack.outfit_id)
    async with SESSION_LOCAL() as db:
        row = await db.get(CompanionActionPack, pack.id)
        if row is None:
            raise _GenerationRowGoneError
        outfit = await outfit_snapshot(db, row.user_id, row.outfit_id)
        description = (outfit.description or "") if outfit else ""
        context = context.model_copy(update={"outfit_description": description, "outfit_description_frozen": True})
        row.context_json = context.model_dump_json()
        row.outfit_snapshot = json.dumps({"description": description}, ensure_ascii=False)
        await db.commit()
    return context


async def _require_current_identity_image(context: GenerationContext, avatar: AvatarSnapshot | None) -> None:
    """已制作的包只在身份图变化后失效：冻结的全身身份图须与当前已采纳全身图一致，角色卡文字修订不影响。"""
    if avatar is None or avatar.id != context.identity.avatar_id:
        raise VideoPackStateError("该动作包对应的角色形象已切换，请重新生成动作包")
    frozen = await _process_thread(read_portrait_bytes, context.identity_reference_path)
    current = await _process_thread(read_portrait_bytes, avatar.seed_fullbody_url)
    if frozen is None or current is None:
        raise VideoPackStateError("全身形象无法读取，暂不能启用该动作包")
    if frozen[0] != current[0]:
        raise VideoPackStateError("动作包使用旧身体资料，请先制作新的动作包")
