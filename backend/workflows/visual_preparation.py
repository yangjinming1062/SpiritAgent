"""当前造型的有限基础动画准备；不参与生活决策，也不自动重试失败任务。"""

import asyncio
import hashlib
import json
from typing import Literal, NamedTuple

from features.action_library.queries import action_by_preset_key
from features.appearance.queries import initial_outfit_snapshot, ready_outfit_snapshot
from features.visuals.generation import wait_visual_job
from features.visuals.queries import (
    creation_request,
    current_outfit_reference,
    job_review_reason,
    reusable_actor_clip,
)
from features.visuals.schemas import ActorClipSource, VisualSnapshot
from features.visuals.service import create_visual_job
from infrastructure.assets.write_protection import user_asset_lock
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from runtime.paid_work import GenerationWorkPaused, require_new_generation_call
from runtime.task_supervisor import TASK_SUPERVISOR

from workflows.visuals_companion import load_visual_snapshot

logger = get_logger(__name__)

# 待机循环是首次可用的最小集合；成功后按渐进集合补充互动反应与常用循环，
# 顺序为先补齐互动反应，再补持续循环，最后才是大幅度的自发动作。
_CORE_PRESETS = ("idle",)
_PROGRESSIVE_PRESETS = ("look", "smile", "focus", "relax", "stretch")
_TASKS: dict[int, asyncio.Task[None]] = {}
_RERUN: set[int] = set()
_STOPPING = False


class _Prepared(NamedTuple):
    """单个动作在本造型下的准备结果；`state` 说明能否自动使用或需要重新排期。"""

    target: tuple[str, str]
    state: Literal["prepared", "manual_only", "switched"]


def _request_id(snapshot: VisualSnapshot, source: ActorClipSource) -> str:
    # 名称、描述分析与更新时间变化不重置同一素材的准备预算。
    basis = {
        "identity_hash": source.identity_hash,
        "portrait_hash": source.portrait_hash,
        "identity_reference_role": source.identity_reference_role,
        "outfit_hash": source.outfit.content_hash,
        "action": source.action.media_basis(),
        "target": (snapshot.target_width, snapshot.target_height),
    }
    return "base-motion:" + hashlib.sha256(json.dumps(basis, sort_keys=True).encode()).hexdigest()


async def _prepare_action(user_id: int, key: str, target: tuple[str, str] | None) -> _Prepared | None:
    """准备单个内置动作；返回本造型应继续绑定的身份、造型与准备结果。

    返回 None 表示素材或任务当前不可用（失败、暂停、未知或资料变化），本轮到此
    为止，交给原任务的恢复入口。
    """

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
                return None
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
            return None
        if current is not None and current.content_hash != source.outfit.content_hash:
            return None
        selected = (source.identity_hash, source.outfit.content_hash)
        if target is not None and selected != target:
            return _Prepared(selected, "switched")
        if await reusable_actor_clip(user_id, source):
            return _Prepared(selected, "prepared")
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
    if await wait_visual_job(user_id, job.id) != "succeeded":
        return None
    # 制作完成不等于可自动演出：成品照常保留在影像库，只有实际语义确认合格才计入准备。
    if await reusable_actor_clip(user_id, source):
        return _Prepared(selected, "prepared")
    logger.info(
        "Base motion kept but not automatically usable",
        extra={
            "user_id": user_id,
            "preset": key,
            "job_id": job.id,
            "review_reason": await job_review_reason(user_id, job.id),
        },
    )
    return _Prepared(selected, "manual_only")


async def _prepare(user_id: int) -> None:
    target: tuple[str, str] | None = None
    for key in (*_CORE_PRESETS, *_PROGRESSIVE_PRESETS):
        require_new_generation_call(user_id)
        # 默认循环成功后才继续渐进集合；任一项失败、暂停和未知都终止本轮，
        # 留给原任务的恢复入口，不逐项消耗预算。
        result = await _prepare_action(user_id, key, target)
        if result is None:
            return
        if result.state == "switched":
            schedule_base_motion_preparation(user_id)
            return
        target = result.target
        if result.state == "prepared":
            continue
        # 已产出成品但不可自动使用：待机循环未达成核心目标时结束本轮；渐进动作继续
        # 下一项，同一请求不会再次付费生成。
        if key in _CORE_PRESETS:
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
