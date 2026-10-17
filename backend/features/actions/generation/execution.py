"""动作制作：execution。"""

import asyncio
import tempfile
from pathlib import Path

from infrastructure.database import SESSION_LOCAL
from infrastructure.video_processing.ffmpeg import VideoProcessError
from runtime.paid_work import GenerationWorkPaused
from runtime.user_maintenance import is_user_in_maintenance
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.actions.generation.script import parse_action_script_entry
from features.actions.generation.state import GenerationContext
from features.actions.materials import accept_action_asset, accepted_action_asset
from features.actions.media_contracts import ActionResult
from features.actions.models import CompanionAction, CompanionActionPack
from features.appearance.avatar_service import get_avatar_job_lock

from .catalog import _fulfill_pending_intents, _publish_dynamic_catalog, _publish_ready
from .errors import VideoPackError, _generation_error, _GenerationRowGoneError
from .execution_state import _ACTION_INFLIGHT, _action_pipeline_semaphore, _pack_finalize_lock, logger
from .helpers import _clip_row_values, _pack_dir, _process_thread, _source_ext
from .pipeline import _prepare_clip, _prepare_image, _run_action_pipeline
from .planning import _compose_scripts, _prepare_action_video_plan
from .queries import _load_generation_context, _must_succeed_actions
from .references import _freeze_outfit_description, _prepare_pack_identity, require_action_matting_model
from .transactions import _emit_pack_event, _fail_dynamic_jobs, _fail_pack, _mark_pack_failed, _record_action_failure


async def _claim_generation_jobs(db: AsyncSession, jobs: list[CompanionAction]) -> list[CompanionAction]:
    reserved = {job.id for job in jobs if job.status in ("queued", "processing") and job.id not in _ACTION_INFLIGHT}
    if not reserved:
        return []
    _ACTION_INFLIGHT.update(reserved)
    claimed: set[int] = set()
    try:
        # 首次查询等待期间旧任务可能已完成；领取后重新读当前行，不能用旧快照启动下一次付费步骤。
        current = (
            await db.scalars(
                select(CompanionAction)
                .where(CompanionAction.id.in_(reserved), CompanionAction.status.in_(("queued", "processing")))
                .order_by(CompanionAction.id)
                .execution_options(populate_existing=True),
            )
        ).all()
        claimed.update(job.id for job in current)
        return list(current)
    finally:
        _ACTION_INFLIGHT.difference_update(reserved - claimed)


async def _build_pack(
    pack_id: int,
    *,
    clips: dict[str, tuple[bytes, str]],
    canvas: tuple[int, int],
    action_ranges: dict[str, tuple[float, float]],
) -> None:
    """后台构建（上传导入）：逐动作处理并落库 → 发布。"""
    canvas_w, canvas_h = canvas
    try:
        async with SESSION_LOCAL() as db:
            pack = await db.get(CompanionActionPack, pack_id)
            if pack is None:
                return
            user_id = pack.user_id

        results: dict[str, ActionResult] = {}
        with tempfile.TemporaryDirectory(prefix="video-pack-") as tmp:
            work = Path(tmp)
            for action, (data, content_type) in clips.items():
                src = work / f"{action}_src{_source_ext(content_type)}"
                await _process_thread(src.write_bytes, data)
                start, end = action_ranges.get(action, (None, None))
                if content_type.startswith("image/"):
                    result = await _prepare_image(
                        work,
                        action,
                        src,
                        user_id,
                        canvas_w,
                        canvas_h,
                        directory=_pack_dir(pack),
                    )
                else:
                    result = await _prepare_clip(
                        work,
                        action,
                        src,
                        user_id,
                        canvas_w,
                        canvas_h,
                        start=start,
                        end=end,
                        directory=_pack_dir(pack),
                    )
                results[action] = result
                async with SESSION_LOCAL() as db:
                    job = await db.scalar(
                        select(CompanionAction).where(
                            CompanionAction.pack_id == pack_id,
                            CompanionAction.key == action,
                        ),
                    )
                    if job is None:
                        raise _GenerationRowGoneError
                    job.status, job.stage = "succeeded", "publish"
                    job.loopable = True if result.clip.media_type == "video" else None
                    for key, value in _clip_row_values(result).items():
                        setattr(job, key, value)
                    accept_action_asset(job)
                    await db.commit()

        await _publish_ready(pack_id, results)
    except asyncio.CancelledError:
        # 用户维护或停机中断导入：上传源片段不可复用，与重启恢复一致按失败落库，避免包一直占着构建位。
        await _fail_pack(pack_id, "处理已中断，请重新提交")
        raise
    except Exception as exc:  # 后台任务兜底：失败必须落库可见
        logger.exception("video pack build failed", extra={"pack_id": pack_id})
        # 只有处理错误自带公开文案；其余异常（含路径等内部信息）只进日志。
        public = str(exc) if isinstance(exc, (VideoProcessError, VideoPackError)) else "动作片段处理失败，请重新提交"
        await _fail_pack(pack_id, public)


async def _generate_pack(pack_id: int) -> None:
    """成功动作独立落盘；重启及重试不重做已成功动作，不重复提交未知付费请求。"""
    claimed_ids: set[int] = set()
    jobs: list[CompanionAction] = []
    try:
        async with SESSION_LOCAL() as db:
            pack = await db.get(CompanionActionPack, pack_id)
            if pack is None or pack.status != "processing":
                return
            jobs = (
                (
                    await db.execute(
                        select(CompanionAction).where(CompanionAction.pack_id == pack_id).order_by(CompanionAction.id),
                    )
                )
                .scalars()
                .all()
            )
            jobs = await _claim_generation_jobs(db, list(jobs))
            claimed_ids.update(job.id for job in jobs)
            await db.refresh(pack)
        context = _load_generation_context(pack)
        if context is None:
            raise VideoPackError("动作包的制作资料不完整，请重新生成动作包")
        context = await _freeze_outfit_description(pack, context)
        pending = [job for job in jobs if not job.script_json]
        for job in pending:
            await _prepare_action_video_plan(job)
        if context.reference_alignment != "ready" or pending:
            # 参考校准与脚本撰写都是付费调用：抠像模型缺失时整包在付费前失败，已有进度留待续跑。
            require_action_matting_model()
        context = await _prepare_pack_identity(pack, context)
        if pending:
            await _emit_pack_event(
                pack.user_id,
                "companion.video.progress",
                {"packId": pack_id, "outfitId": pack.outfit_id, "stage": "script"},
            )
            await _compose_scripts(pack, context, pending)
        sem = _action_pipeline_semaphore(pack_id)

        async def run_one(job: CompanionAction) -> None:
            try:
                async with sem:
                    if job.status == "queued" and is_user_in_maintenance(pack.user_id):
                        return
                    entry = parse_action_script_entry(job.script_json or "")
                    await _run_action_pipeline(pack, job, entry, context)
            except GenerationWorkPaused:
                return
            except Exception as exc:  # 动作失败隔离，其他已请求动作仍可交付并独立重做
                try:
                    await _record_action_failure(pack_id, job, exc)
                except Exception:  # 失败记录自身失败不能中断其余在制动作的收敛
                    logger.exception(
                        "action failure record failed",
                        extra={"pack_id": pack_id, "action_id": job.id},
                    )
            finally:
                claimed_ids.discard(job.id)
                _ACTION_INFLIGHT.discard(job.id)

        await asyncio.gather(*(run_one(job) for job in jobs))
        await _finalize_pack_generation(pack_id)
    except GenerationWorkPaused:
        return
    except _GenerationRowGoneError:
        logger.info("video pack left generation", extra={"pack_id": pack_id})
    except Exception as exc:  # 后台异常必须转为持久可见失败
        logger.exception("video pack generation failed", extra={"pack_id": pack_id})
        # 只结束本任务持有的动作，新受理的并行动作仍保留自己的供应商句柄。
        for job in jobs:
            if job.id in claimed_ids:
                await _record_action_failure(pack_id, job, exc)
        await _finalize_pack_generation(pack_id)
    finally:
        _ACTION_INFLIGHT.difference_update(claimed_ids)


async def _finalize_pack_generation(pack_id: int) -> None:
    """同包动作全部收敛后统一发布；失败判定与新请求受理共用账户锁。"""
    async with _pack_finalize_lock(pack_id):
        async with SESSION_LOCAL() as db:
            pack = await db.get(CompanionActionPack, pack_id)
            if pack is None or pack.status != "processing":
                return
            user_id = pack.user_id
        async with get_avatar_job_lock(user_id), SESSION_LOCAL() as db:
            pack = await db.get(CompanionActionPack, pack_id)
            if pack is None or pack.status != "processing" or is_user_in_maintenance(user_id):
                return
            rows = (await db.scalars(select(CompanionAction).where(CompanionAction.pack_id == pack_id))).all()
            if any(job.status in ("queued", "processing") for job in rows):
                return
            context = _load_generation_context(pack)
            must_actions = _must_succeed_actions(context.must_actions) if context is not None else set()
            blocking = [job for job in rows if accepted_action_asset(job) is None and job.key in must_actions]
            available = {job.key for job in rows if accepted_action_asset(job) is not None}
            if context is None or blocking or not must_actions.issubset(available):
                message = (
                    "动作包的制作资料不完整，请重新生成动作包"
                    if context is None
                    else "；".join(dict.fromkeys(job.error or "动作未完成" for job in blocking)) or "动作未完成"
                )
                await _mark_pack_failed(db, pack, message[:500])
                await db.commit()
                return
            results = {
                job.key: ActionResult.model_validate_json(material.result_json)
                for job in rows
                if (material := accepted_action_asset(job)) is not None
            }
        await _emit_pack_event(
            user_id,
            "companion.video.progress",
            {"packId": pack_id, "outfitId": pack.outfit_id, "stage": "publish"},
        )
        await _publish_ready(pack_id, results)


async def _generate_dynamic_actions(
    pack_id: int,
    *,
    action_ids: list[int] | None = None,
    queued_only: bool = False,
) -> bool:
    """按冻结规格制作已受理动作；同包其他制作任务可继续独立推进。"""
    claimed_ids: set[int] = set()
    try:
        async with SESSION_LOCAL() as db:
            pack = await db.get(CompanionActionPack, pack_id)
            if pack is None or pack.status not in ("ready", "processing"):
                return False
            # 结果未知的动作不重新付费；同动作的脚本、句柄和素材由唯一在途任务持有。
            stmt = select(CompanionAction).where(
                CompanionAction.pack_id == pack_id,
                CompanionAction.status.in_(("queued",) if queued_only else ("queued", "processing")),
            )
            if action_ids is not None:
                stmt = stmt.where(CompanionAction.id.in_(action_ids))
            jobs = (await db.execute(stmt.order_by(CompanionAction.id))).scalars().all()
            jobs = await _claim_generation_jobs(db, list(jobs))
            if not jobs:
                return False
            claimed_ids.update(job.id for job in jobs)
            await db.refresh(pack)
        context = _load_generation_context(pack)
        if context is None:
            await _fail_dynamic_jobs(
                pack_id,
                "该动作包的制作资料不完整，请重新生成动作包",
                action_ids=list(claimed_ids),
            )
            await _finalize_pack_generation(pack_id)
            return False
        if context.reference_alignment != "ready":
            return False
        sem = _action_pipeline_semaphore(pack_id)

        async def run_one(job: CompanionAction) -> bool:
            try:
                async with sem:
                    # 等待并发位期间也可能进入维护；已在制作的动作继续收敛。
                    if job.status == "queued" and is_user_in_maintenance(pack.user_id):
                        return False
                    return await _generate_one_dynamic(pack, job, context)
            except GenerationWorkPaused:
                return False
            except Exception as exc:  # 单动作失败隔离，不影响已就绪目录
                try:
                    await _record_action_failure(pack_id, job, exc)
                except Exception:  # 失败记录自身失败不能中断其余在制动作的收敛
                    logger.exception(
                        "action failure record failed",
                        extra={"pack_id": pack_id, "action_id": job.id},
                    )
                return False
            finally:
                claimed_ids.discard(job.id)
                _ACTION_INFLIGHT.discard(job.id)

        outcomes = await asyncio.gather(*(run_one(job) for job in jobs))
        # 只有新成功的动作改变目录：发布新目录快照（失败与待复核动作不并入），并兑现这些动作制作期间的表达意图。
        if pack.status == "processing":
            await _finalize_pack_generation(pack_id)
        if any(outcomes):
            if pack.status == "ready":
                await _publish_dynamic_catalog(pack_id)
            await _fulfill_pending_intents([job.id for job, succeeded in zip(jobs, outcomes, strict=True) if succeeded])
        return not is_user_in_maintenance(pack.user_id)
    except Exception as exc:  # 后台任务兜底：失败必须落库可见
        logger.exception("dynamic action generation crashed", extra={"pack_id": pack_id})
        await _fail_dynamic_jobs(pack_id, _generation_error(exc), action_ids=list(claimed_ids))
        await _finalize_pack_generation(pack_id)
        return False
    finally:
        _ACTION_INFLIGHT.difference_update(claimed_ids)


async def _generate_one_dynamic(
    pack: CompanionActionPack,
    job: CompanionAction,
    context: GenerationContext,
) -> bool:
    """复用已保存脚本，缺失时按冻结规格撰写并落库，再交给素材管线；返回素材是否直接成功。"""
    await _prepare_action_video_plan(job)
    if job.script_json:
        entry = parse_action_script_entry(job.script_json)
    else:
        # 撰写脚本是本次制作的首个付费调用。
        require_action_matting_model()
        await _emit_pack_event(
            pack.user_id,
            "companion.action.job_updated",
            {"packId": pack.id, "outfitId": pack.outfit_id, "action": job.key, "stage": "script"},
        )
        (entry,) = await _compose_scripts(pack, context, [job])
    return await _run_action_pipeline(pack, job, entry, context)
