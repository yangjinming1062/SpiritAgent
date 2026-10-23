"""影像库的备份表、校验与恢复操作。"""

from functools import partial
from typing import Any

from infrastructure.backup import BackupContext, BackupTable
from runtime.events.emit import emit_ws_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.media.contracts import RemovedVideoTask

from .backup_assets import restore_visual_payload, validate_visual_owned_paths
from .models import VisualItem, VisualJob, VisualPresentation
from .repository import visual_progress
from .schemas import parse_job_progress


def _prepare(table: str, raw: dict[str, Any], payload: dict[str, Any], context: BackupContext) -> None:
    restore_visual_payload(table, payload, context.id_map, target_user_id=context.user_id)


async def _check(
    table: str,
    db: AsyncSession,  # noqa: ARG001  # 契约签名与其他模块一致
    raw: dict[str, Any],  # noqa: ARG001
    payload: dict[str, Any],
    context: BackupContext,  # noqa: ARG001
) -> None:
    validate_visual_owned_paths(payload, context.asset_owner_id)
    if table == "visual_jobs":
        if payload["purpose"] == "regenerate" and (
            payload.get("target_visual_id") is None or not payload.get("expected_version")
        ):
            raise ValueError("重做工作项必须保留目标影像与目标版本。")
        if payload.get("state") == "succeeded" and payload.get("result_visual_id") is None:
            raise ValueError("已完成的制作工作项缺少成品影像。")


async def _link_input_images(
    db: AsyncSession,
    raw_rows: list[dict[str, Any]],
    new_map: dict[str, int | str],
    context: BackupContext,
) -> None:
    """视频行的输入图链接是同表前向引用，整表写入后按原 ID 回填。"""

    for raw in raw_rows:
        original_id = str(raw["id"])
        if ("visual_items", original_id) not in context.rewriter.inserted_rows:
            continue
        mapped = new_map.get(str(raw["input_image_id"])) if raw.get("input_image_id") is not None else None
        if mapped is None:
            continue
        row = await db.get(VisualItem, new_map[original_id])
        if row is not None:
            row.input_image_id = int(mapped)


async def _existing_job(
    db: AsyncSession,
    raw: dict[str, Any],  # noqa: ARG001
    payload: dict[str, Any],
    context: BackupContext,
) -> int | None:
    """按制作进度的 generation_id 识别已导入的工作项，重复合并不叠加制作历史。"""

    progress = parse_job_progress(payload.get("progress_json"))
    if progress is None:
        return None
    for job in await db.scalars(select(VisualJob).where(VisualJob.user_id == context.user_id)):
        saved = visual_progress(job)
        if saved is not None and saved.generation_id == progress.generation_id:
            return job.id
    return None


def backup_tables() -> tuple[BackupTable, ...]:
    return (
        BackupTable(
            # 素材库引用允许缺失：快照冻结资料继续有效，列值在恢复映射里置 None。
            "visual_items",
            VisualItem,
            foreign_keys={},
            unique_keys=("media_kind", "content_hash"),
            strict_references=False,
            merge_unique_on_overwrite=True,
            prepare_payload=partial(_prepare, "visual_items"),
            validate_payload=partial(_check, "visual_items"),
            after_rows=_link_input_images,
        ),
        BackupTable(
            "visual_jobs",
            VisualJob,
            foreign_keys={
                "target_visual_id": "visual_items",
                "result_visual_id": "visual_items",
                "result_image_id": "visual_items",
            },
            unique_keys=None,
            strict_references=True,
            prepare_payload=partial(_prepare, "visual_jobs"),
            validate_payload=partial(_check, "visual_jobs"),
            find_existing=_existing_job,
        ),
        BackupTable(
            "visual_presentations",
            VisualPresentation,
            foreign_keys={"current_visual_id": "visual_items", "loop_visual_id": "visual_items"},
            unique_keys=(),
            strict_references=True,
            merge_unique_on_overwrite=True,
            prepare_payload=partial(_prepare, "visual_presentations"),
            validate_payload=partial(_check, "visual_presentations"),
        ),
    )


def _removed_task(job: VisualJob) -> RemovedVideoTask | None:
    progress = visual_progress(job)
    if progress is None or not progress.video_chain_json:
        return None
    return RemovedVideoTask(task_id=job.id, generation_state_json=progress.video_chain_json)


async def visual_video_tasks_for_removal(db: AsyncSession, user_id: int) -> tuple[RemovedVideoTask, ...]:
    jobs = await db.scalars(select(VisualJob).where(VisualJob.user_id == user_id))
    return tuple(task for job in jobs if (task := _removed_task(job)) is not None)


async def playback_versions(db: AsyncSession, user_id: int) -> tuple[int, int] | None:
    row = (
        await db.execute(
            select(VisualPresentation.state_revision, VisualPresentation.presentation_epoch).where(
                VisualPresentation.user_id == user_id,
            ),
        )
    ).one_or_none()
    return tuple(row) if row is not None else None


async def advance_playback_versions(
    db: AsyncSession,
    user_id: int,
    previous: tuple[int, int] | None,
) -> None:
    """恢复或换号后推进呈现版本，旧待播指令不再被认领。"""

    state = await db.scalar(select(VisualPresentation).where(VisualPresentation.user_id == user_id))
    if state is None:
        return
    revision, epoch = previous or (0, 0)
    state.state_revision = max(state.state_revision, revision) + 1
    state.presentation_epoch = max(state.presentation_epoch, epoch) + 1
    state.pending_command_json = None
    emit_ws_event(db, user_id=user_id, event_type="companion.visual.updated", payload={"version": state.state_revision})
