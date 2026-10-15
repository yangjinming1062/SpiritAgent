"""动作制作：planning。"""

from collections.abc import Sequence

from infrastructure.ai.tasks import AiTaskKey
from infrastructure.database import SESSION_LOCAL
from sqlalchemy import update

from features.actions.generation.script import (
    ActionScriptEntry,
    ActionSpec,
    ImageActionSpec,
    VideoActionSpec,
    compose_action_image_descriptions,
    compose_action_script,
)
from features.actions.generation.state import GenerationContext
from features.actions.models import CompanionAction, CompanionActionPack
from features.actions.schemas import ActionDesignRequest
from features.media.chain import MediaChainState

from .errors import VideoPackError, VideoPackStateError, _GenerationRowGoneError
from .execution_state import _SYSTEM_ACTION_SECONDS
from .helpers import _artifact_abs_path, _image_data_uri, _process_thread
from .references import _freeze_action_video_plan, _require_new_paid_step, _resolve_action_video_plan
from .transactions import _advance_job


async def _prepare_action_video_plan(job: CompanionAction) -> None:
    """脚本付费前固定本次规格，后续步骤沿用同一生成计划。"""
    if job.media_type == "image":
        return
    if job.generation_state_json and MediaChainState.model_validate_json(job.generation_state_json).providers:
        return
    spec = _action_spec(job)
    if not isinstance(spec, VideoActionSpec):
        raise VideoPackStateError("视频动作缺少视频规格")
    seconds, providers = await _resolve_action_video_plan(
        job.user_id,
        needs_loop_frames=spec.clip_kind == "loop",
        duration_seconds=spec.duration_seconds,
    )
    _freeze_action_video_plan(job, seconds, providers)
    await _advance_job(
        job.id,
        stage=job.stage,
        target_duration_seconds=job.target_duration_seconds,
        generation_state_json=job.generation_state_json,
    )


def _action_spec(job: CompanionAction) -> ActionSpec:
    """读取原设计意图；视频的实际制作时长由生成计划绑定。"""
    if job.system_slot:
        if job.media_type == "image":
            return ImageActionSpec(action=job.key, system_slot=job.system_slot)
        return VideoActionSpec(
            action=job.key,
            system_slot=job.system_slot,
            duration_seconds=_SYSTEM_ACTION_SECONDS,
            clip_kind="loop",
        )
    if job.source_design_json is None:
        raise VideoPackError("动态动作缺少提案规格，请重做")
    design = ActionDesignRequest.model_validate_json(job.source_design_json)
    return VideoActionSpec(
        action=job.key,
        name=design.name,
        semantics=design.motion_description,
        use_when=design.use_when,
        avoid_when=design.avoid_when,
        duration_seconds=design.duration_seconds,
        clip_kind=design.clip_kind,
    )


async def _compose_scripts(
    pack: CompanionActionPack,
    context: GenerationContext,
    jobs: Sequence[CompanionAction],
) -> list[ActionScriptEntry]:
    """按媒体类型撰写并逐批保存描述，续跑复用已完成批次；返回顺序与 jobs 一致。"""
    image_specs: list[ImageActionSpec] = []
    video_specs: list[VideoActionSpec] = []
    for job in jobs:
        spec = _action_spec(job)
        values: dict[str, object] = {"feedback": context.action_feedback.get(job.key, "")}
        if isinstance(spec, VideoActionSpec):
            values["duration_seconds"] = job.target_duration_seconds
            video_specs.append(spec.model_copy(update=values))
        else:
            image_specs.append(spec.model_copy(update=values))
    reference = await _process_thread(_image_data_uri, _artifact_abs_path(pack.reference_path))
    entries: dict[str, ActionScriptEntry] = {}
    jobs_by_key = {job.key: job for job in jobs}

    async def save_entries(batch: Sequence[ActionScriptEntry]) -> None:
        # 下一批付费调用可能暂停或失败，先保存这一批已完成的描述。
        scripts = {entry.action: entry.model_dump_json() for entry in batch}
        async with SESSION_LOCAL() as db:
            for key, script in scripts.items():
                saved_id = await db.scalar(
                    update(CompanionAction)
                    .where(CompanionAction.id == jobs_by_key[key].id)
                    .values(script_json=script)
                    .returning(CompanionAction.id),
                )
                if saved_id is None:
                    raise _GenerationRowGoneError
            await db.commit()
        for entry in batch:
            jobs_by_key[entry.action].script_json = scripts[entry.action]
            entries[entry.action] = entry

    if image_specs:
        images = await compose_action_image_descriptions(
            pack.user_id,
            task=AiTaskKey.window_action,
            reference_image=reference,
            identity=context.identity,
            persona_definition=context.persona_definition,
            personality_tags=context.personality_tags,
            outfit_description=context.outfit_description,
            specs=image_specs,
            feedback=context.feedback,
            before_submit=lambda: _require_new_paid_step(pack, context),
        )
        await save_entries(images.actions)
    if video_specs:
        videos = await compose_action_script(
            pack.user_id,
            task=AiTaskKey.window_action,
            reference_image=reference,
            identity=context.identity,
            persona_definition=context.persona_definition,
            personality_tags=context.personality_tags,
            outfit_description=context.outfit_description,
            specs=video_specs,
            feedback=context.feedback,
            before_submit=lambda: _require_new_paid_step(pack, context),
        )
        await save_entries(videos.actions)
    return [entries[job.key] for job in jobs]
