"""当前造型的有限基础动画准备；不参与生活决策，也不自动重试失败任务。"""

import asyncio
import hashlib
import json

from features.action_library.queries import action_by_preset_key
from features.appearance.queries import initial_outfit_snapshot, ready_outfit_snapshot
from features.visuals.generation import wait_visual_job
from features.visuals.queries import creation_request, current_outfit_reference, reusable_actor_clip
from features.visuals.schemas import ActorClipSource, VisualSnapshot
from features.visuals.service import create_visual_job
from infrastructure.assets.write_protection import user_asset_lock
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from runtime.paid_work import GenerationWorkPaused, require_new_generation_call
from runtime.task_supervisor import TASK_SUPERVISOR

from workflows.visuals_companion import load_visual_snapshot

logger = get_logger(__name__)
_PRESETS = ("idle", "look", "stretch")
_TASKS: dict[int, asyncio.Task[None]] = {}
_RERUN: set[int] = set()
_STOPPING = False


def _request_id(snapshot: VisualSnapshot, source: ActorClipSource) -> str:
    # 名称、描述分析与更新时间变化不重置同一素材的准备预算。
    basis = {
        "identity": source.identity.model_dump(mode="json"),
        "identity_hash": source.identity_hash,
        "portrait_hash": source.portrait_hash,
        "identity_reference_role": source.identity_reference_role,
        "outfit_id": source.outfit.material_id,
        "outfit_hash": source.outfit.content_hash,
        "action": source.action.model_dump(mode="json", exclude={"name", "revision"}),
        "target": (snapshot.target_width, snapshot.target_height),
        "requirements": source.requirements,
    }
    return "base-motion:" + hashlib.sha256(json.dumps(basis, sort_keys=True).encode()).hexdigest()


async def _prepare(user_id: int) -> None:
    target: tuple[str, str] | None = None
    for key in _PRESETS:
        require_new_generation_call(user_id)
        async with user_asset_lock(user_id):
            current = await current_outfit_reference(user_id)
            async with SESSION_LOCAL() as db:
                outfit = (
                    await ready_outfit_snapshot(db, user_id, current.material_id)
                    if current is not None and current.material_id is not None
                    else await initial_outfit_snapshot(db, user_id)
                    if current is None
                    else None
                )
                action = await action_by_preset_key(db, user_id, key)
                if outfit is None or action is None or action.status != "approved":
                    return
                snapshot = await load_visual_snapshot(
                    db,
                    user_id,
                    asset_kind="actor",
                    outfit_id=outfit.id,
                    scene_id=None,
                    action_id=action.id,
                    target_width=1024,
                    target_height=1024,
                    requirements="按参考中的实际身体结构表现这项动作，缺少的身体部位及不适用的生理动态不需要表现；全身完整入画。",
                )
            source = snapshot.source
            if not isinstance(source, ActorClipSource):
                return
            if current is not None and current.content_hash != source.outfit.content_hash:
                return
            selected = (source.identity_hash, source.outfit.content_hash)
            if target is not None and selected != target:
                schedule_base_motion_preparation(user_id)
                return
            target = selected
            if await reusable_actor_clip(user_id, source):
                continue
            request_id = _request_id(snapshot, source)
            original = await creation_request(user_id, request_id)
            if original is not None:
                job = original[0]
            else:
                job = await create_visual_job(
                    user_id,
                    snapshot=snapshot,
                    target_kind="video",
                    source="base_preparation",
                    request_id=request_id,
                )
        # 默认循环成功后才准备反应；失败、暂停和未知均留给原任务的恢复入口。
        if await wait_visual_job(user_id, job.id) != "succeeded":
            return


async def _run(user_id: int) -> None:
    try:
        await _prepare(user_id)
    except GenerationWorkPaused:
        return
    except Exception:
        logger.warning("Base motion preparation unavailable", extra={"user_id": user_id}, exc_info=True)


def schedule_base_motion_preparation(user_id: int) -> None:
    if _STOPPING:
        return
    if user_id in _TASKS:
        _RERUN.add(user_id)
        return
    task = asyncio.create_task(_run(user_id), name=f"visuals.preparation.{user_id}")
    _TASKS[user_id] = task
    TASK_SUPERVISOR.register(task, owner="visuals.preparation", user_id=user_id)

    def finished(done: asyncio.Task[None]) -> None:
        if _TASKS.get(user_id) is done:
            _TASKS.pop(user_id)
        if user_id in _RERUN:
            _RERUN.discard(user_id)
            if not done.cancelled():
                schedule_base_motion_preparation(user_id)

    task.add_done_callback(finished)


async def stop_base_motion_preparation() -> None:
    global _STOPPING
    _STOPPING = True
    _RERUN.clear()
    await TASK_SUPERVISOR.drain(owner="visuals.preparation")


async def start_base_motion_preparation() -> None:
    global _STOPPING
    _STOPPING = False


async def resume_base_motion_preparation(user_id: int) -> None:
    schedule_base_motion_preparation(user_id)
