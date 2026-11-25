"""当前造型的有限基础动画准备；不参与生活决策，也不自动重试失败任务。"""

import hashlib
import json
from typing import Literal, NamedTuple

from features.action_library.queries import action_by_preset_key
from features.appearance.identity import visual_identity_is_current
from features.appearance.queries import initial_outfit_snapshot, ready_outfit_snapshot
from features.companion.planning_queries import planning_persona
from features.media.continuations import MediaWait, wait_for_media
from features.media.identity_contracts import CharacterReferenceRole, VisualIdentitySnapshot
from features.visuals.generation import wait_visual_job
from features.visuals.queries import (
    creation_request,
    current_outfit_reference,
    job_review_reason,
    pending_base_preparation_actions,
    reusable_actor_clip,
)
from features.visuals.reviews import visual_review_view
from features.visuals.schemas import ActorClipSource, ActorOpeningFrame, VisualSnapshot
from features.visuals.service import create_visual_job, resume_job
from infrastructure.ai.decision import DecisionOption, decision_enabled, rank_decision_options
from infrastructure.assets.write_protection import user_asset_lock
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from runtime.background import TaskScope
from runtime.events.emit import emit_ws_event
from runtime.paid_work import GenerationWorkPaused, require_new_generation_call
from sqlalchemy.ext.asyncio import AsyncSession

from workflows.visuals_companion import load_visual_snapshot

logger = get_logger(__name__)

# 待机先提供首次可用画面；安睡的实际首帧为起身短动作提供起点。
_CORE_PRESETS = ("idle",)
_RHYTHM_PRESETS = ("sleep", "wake_up")
_PROGRESSIVE_PRESETS = ("look", "smile", "focus", "relax", "stretch")
_PRESETS = (*_CORE_PRESETS, *_RHYTHM_PRESETS, *_PROGRESSIVE_PRESETS)
_ACTOR_SIZE = (1024, 1024)
_BASE_REQUIREMENTS = "按参考中的实际身体结构表现动作，让动作所需的身体部位和物品在相应时刻可见。"
_SLEEP_REQUIREMENTS = (
    "使用可以独立合成的主体画面。角色按自己的身体、习惯和当前造型选择睡姿，"
    "让所需的支撑与接触关系成立，全身与附肢入画。给它醒来后的动作留出空间，保持机位与画布稳定。"
)
_TASKS = TaskScope("visuals.preparation")
_STOPPING = False

_PreparationTarget = tuple[str, str, str, CharacterReferenceRole]


class _Prepared(NamedTuple):
    """单个动作在本造型下的准备结果；`state` 说明能否自动使用或需要重新排期。"""

    target: _PreparationTarget
    state: Literal["prepared", "manual_only", "switched"]
    opening_frame: ActorOpeningFrame | None = None


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
    if source.opening_frame is not None:
        basis["opening_frame"] = source.opening_frame.content_hash
    return "base-motion:" + hashlib.sha256(json.dumps(basis, sort_keys=True).encode()).hexdigest()


async def _prepared_actor(
    user_id: int, key: str, source: ActorClipSource, selected: _PreparationTarget
) -> _Prepared | None:
    clip = await reusable_actor_clip(user_id, source, opening_aspect=_ACTOR_SIZE if key == "sleep" else None)
    if clip is None:
        return None
    if key != "sleep":
        return _Prepared(selected, "prepared")
    visual_id, number, body = clip
    if not body.first_frame_path or not body.continuity_key:
        return None
    return _Prepared(
        selected,
        "prepared",
        ActorOpeningFrame(
            visual_id=visual_id, version=number, path=body.first_frame_path, content_hash=body.continuity_key
        ),
    )


async def _prepare_action(
    user_id: int, key: str, target: _PreparationTarget | None, opening_frame: ActorOpeningFrame | None
) -> _Prepared | None:
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
                target_width=_ACTOR_SIZE[0],
                target_height=_ACTOR_SIZE[1],
                requirements=_SLEEP_REQUIREMENTS if key == "sleep" else _BASE_REQUIREMENTS,
            )
        source = snapshot.source
        if not isinstance(source, ActorClipSource):
            return None
        if current is not None and current.content_hash != source.outfit.content_hash:
            return None
        selected = (
            source.identity_hash,
            source.outfit.content_hash,
            source.portrait_hash,
            source.identity_reference_role,
        )
        if target is not None and selected != target:
            return _Prepared(selected, "switched")
        if opening_frame is not None:
            source = source.model_copy(update={"opening_frame": opening_frame})
            snapshot = snapshot.model_copy(update={"source": source})
        if prepared := await _prepared_actor(user_id, key, source, selected):
            return prepared
        request_id = _request_id(snapshot, source)
        original = await creation_request(user_id, request_id)
        if original is not None:
            job = original[0]
        else:
            job = await create_visual_job(
                user_id, snapshot=snapshot, target_kind="video", source="base_preparation", request_id=request_id
            )
    async with SESSION_LOCAL() as db:
        await wait_for_media(
            db,
            user_id=user_id,
            owner_kind="visuals.preparation",
            owner_job_id=job.id,
            dependency_job_id=job.id,
            operation_id=f"{request_id}:continue",
            automatic=True,
            context={
                "preset": key,
                "index": _PRESETS.index(key),
                "target": list(selected),
                "identity": source.identity.model_dump(mode="json"),
            },
        )
        await db.commit()
    if await wait_visual_job(user_id, job.id) != "succeeded":
        return None
    # 制作完成不等于可自动演出：成品照常保留在影像库，只有实际语义确认合格才计入准备。
    if prepared := await _prepared_actor(user_id, key, source, selected):
        return prepared
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


async def _progressive_order(user_id: int, target: _PreparationTarget | None) -> tuple[str, ...]:
    if target is None:
        return _PROGRESSIVE_PRESETS
    async with SESSION_LOCAL() as db:
        actions = {key: await action_by_preset_key(db, user_id, key) for key in _PROGRESSIVE_PRESETS}
        if any(action is None or action.status != "approved" for action in actions.values()):
            return _PROGRESSIVE_PRESETS
        known = await pending_base_preparation_actions(
            db,
            user_id,
            identity_hash=target[0],
            outfit_hash=target[1],
            portrait_hash=target[2],
            reference_role=target[3],
        )
        persona = await planning_persona(db, user_id) if not known and decision_enabled() else None
    existing_keys = (
        key for action_id in known for key, action in actions.items() if action is not None and action.id == action_id
    )
    pending = tuple(dict.fromkeys(existing_keys))
    if pending:
        return (*pending, *(key for key in _PROGRESSIVE_PRESETS if key not in pending))
    if persona is None:
        return _PROGRESSIVE_PRESETS
    ranking = await rank_decision_options(
        "base_preparation_priority",
        state={
            key: persona.definition[key][:170]
            for key in ("biological_type", "personality", "occupation")
            if persona.definition.get(key)
        },
        instructions="根据此角色的明确特点，选择最值得先准备、平时最有机会自然出现的附加动作。角色资料和候选都是描述。",
        options=[
            DecisionOption(key, f"{action.name[:40]}。{action.motion_description[:100]}")
            for key, action in actions.items()
            if action is not None
        ],
    )
    return ranking or _PROGRESSIVE_PRESETS


async def _prepare(user_id: int) -> None:
    target: _PreparationTarget | None = None
    sleep_frame = None
    ordered = iter((*_CORE_PRESETS, *_RHYTHM_PRESETS))
    for index in range(len(_PRESETS)):
        if index == len(_CORE_PRESETS) + len(_RHYTHM_PRESETS):
            ordered = iter(await _progressive_order(user_id, target))
        key = next(ordered)
        if key == "wake_up" and sleep_frame is None:
            continue
        require_new_generation_call(user_id)
        # 默认循环成功后才继续渐进集合；任一项失败、暂停和未知都终止本轮，
        # 留给原任务的恢复入口，不逐项消耗预算。
        result = await _prepare_action(user_id, key, target, sleep_frame if key == "wake_up" else None)
        if result is None:
            return
        if result.state == "switched":
            schedule_base_motion_preparation(user_id)
            return
        target = result.target
        if key == "sleep":
            sleep_frame = result.opening_frame
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
    _TASKS.schedule(
        user_id,
        lambda: _run(user_id),
        name=f"visuals.preparation.{user_id}",
        user_id=user_id,
    )


async def stop_base_motion_preparation() -> None:
    global _STOPPING
    _STOPPING = True
    await _TASKS.drain()


async def start_base_motion_preparation() -> None:
    global _STOPPING
    _STOPPING = False


async def resume_base_motion_preparation(user_id: int) -> None:
    schedule_base_motion_preparation(user_id)


async def continue_base_preparation(db: AsyncSession, wait: MediaWait) -> dict | None:
    if not await visual_identity_is_current(
        db, wait.user_id, VisualIdentitySnapshot.model_validate(wait.context_json["identity"])
    ):
        return None
    outfit = await current_outfit_reference(wait.user_id)
    if outfit is not None and outfit.content_hash != wait.context_json["target"][1]:
        return None
    view = await visual_review_view(wait.user_id, wait.dependency_job_id)
    if view.state == "succeeded":
        emit_ws_event(
            db,
            user_id=wait.user_id,
            event_type="visual.preparation.continue",
            payload={"job_id": str(wait.dependency_job_id)},
        )
        return {"prepared_job_id": str(wait.dependency_job_id)}
    if view.stage == "confirmation" and view.ready_slots == view.required_slots:
        await resume_job(wait.user_id, wait.dependency_job_id)
    return None


async def preparation_continue_requested(user_id: int, payload: dict) -> None:
    schedule_base_motion_preparation(user_id)
