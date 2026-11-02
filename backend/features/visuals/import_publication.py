"""分析结果追加不可变版本，并在同一事务完成分析任务。"""

from typing import Literal

from infrastructure.database import SESSION_LOCAL
from sqlalchemy import select

from .import_jobs import ImportCatalogCandidate, ImportWork
from .import_operations import import_operations
from .models import VisualImportJob
from .publication import publish_import_revision
from .repository import VisualStateError, notify_visuals_updated, visual_item
from .schemas import (
    ActorClipSource,
    AnalyzingVisualSource,
    CinematicSource,
    ImportPlaybackBlock,
    SceneMediaSource,
    VisualAsset,
)


def import_playback_blocks(
    work: ImportWork, selected: list[ImportCatalogCandidate], verdict: str, reason: str
) -> list[ImportPlaybackBlock]:
    if work.result is None:
        raise VisualStateError("上传分析结果缺失")
    understanding = work.result.understanding
    blocks: list[ImportPlaybackBlock] = []
    if any(entry.status not in {"created", "reused", "skipped", "undone"} for entry in work.result.components):
        blocks.append(ImportPlaybackBlock(code="materials_incomplete", reason="素材整理尚未完成"))
    if work.context.animated_image:
        blocks.append(ImportPlaybackBlock(code="animated", reason="动图原件可预览，暂不支持自动播放"))
    scenes = {entry.material_id for entry in selected if entry.scene is not None}
    outfits = {entry.material_id for entry in selected if entry.outfit is not None}
    actions = {entry.material_id: entry.action for entry in selected if entry.action is not None}
    if understanding.asset_kind in {"scene", "cinematic"} and len(scenes) != 1:
        blocks.append(ImportPlaybackBlock(code="scene", reason="需要确认与整段影像相符的环境"))
    if understanding.asset_kind == "scene":
        if understanding.identity_match != "absent":
            blocks.append(ImportPlaybackBlock(code="identity", reason=understanding.identity_reason))
    else:
        if understanding.identity_match != "same" or work.context.identity is None:
            blocks.append(ImportPlaybackBlock(code="identity", reason=understanding.identity_reason))
        if len(outfits) != 1:
            blocks.append(ImportPlaybackBlock(code="outfit", reason="需要确认与影像相符的着装"))
        if len(actions) != 1:
            blocks.append(ImportPlaybackBlock(code="action", reason="需要确认整段影像的动作或持续姿态"))
    semantics = understanding.media_semantics
    if (understanding.asset_kind != "scene" or work.media_kind == "video") and (
        semantics is None or not semantics.auto_selectable
    ):
        blocks.append(ImportPlaybackBlock(code="semantics", reason="影像的整体含义或适用情境尚不明确"))
    if work.media_kind == "video":
        action = next(iter(actions.values())) if len(actions) == 1 else None
        if (understanding.asset_kind == "scene" and understanding.clip_kind != "loop") or (
            understanding.asset_kind != "scene" and (action is None or understanding.clip_kind != action.clip_kind)
        ):
            blocks.append(ImportPlaybackBlock(code="timing", reason="视频时序尚不符合对应动作的播放方式"))
    if verdict != "pass" and not any(entry.code == "materials_incomplete" for entry in blocks):
        blocks.append(ImportPlaybackBlock(code="review", reason=reason))
    return blocks


async def complete_import_analysis(
    work: ImportWork, selected: list[ImportCatalogCandidate], verdict: Literal["pass", "review", "fail"], reason: str
) -> bool:
    if (
        work.result is None
        or work.snapshot.source.original_path is None
        or work.snapshot.source.original_content_hash is None
    ):
        raise VisualStateError("上传分析结果缺失")
    understanding = work.result.understanding
    outfit = next((entry.outfit for entry in selected if entry.outfit is not None), None)
    scene = next((entry.scene for entry in selected if entry.scene is not None), None)
    actions = [entry.action for entry in selected if entry.action is not None]
    action = actions[0] if len({entry.action_id for entry in actions}) == 1 else None
    work.result.presentation_verdict = verdict
    work.result.blocking_reasons = import_playback_blocks(work, selected, verdict, reason)
    approved = not work.result.blocking_reasons
    original = work.snapshot.source
    metadata = {
        key: value
        for key, value in original.model_dump(mode="python").items()
        if key
        in {
            "original_filename",
            "original_path",
            "original_content_hash",
            "original_width",
            "original_height",
            "original_duration_ms",
        }
    }
    common = {
        **metadata,
        "analysis_job_id": work.id,
        "analysis_pending": False,
        "presentation_approved": bool(approved),
        "analysis_revision": None,
        "identity_reason": understanding.identity_reason,
        "media_semantics": understanding.media_semantics,
    }
    identity_reference_role = getattr(original, "identity_reference_role", "fullbody")
    if understanding.asset_kind == "scene" and scene is not None:
        source = SceneMediaSource(
            scene=scene,
            **{key: value for key, value in common.items() if key != "media_semantics"},
        )
    elif (
        understanding.asset_kind == "actor"
        and work.context.identity is not None
        and work.context.identity_path
        and work.context.identity_hash
        and work.context.portrait_path
        and work.context.portrait_hash
        and outfit is not None
        and action is not None
    ):
        source = ActorClipSource(
            identity=work.context.identity,
            identity_path=work.context.identity_path,
            identity_hash=work.context.identity_hash,
            portrait_path=work.context.portrait_path,
            portrait_hash=work.context.portrait_hash,
            identity_reference_role=identity_reference_role,
            outfit=outfit,
            action=action,
            **common,
        )
    elif (
        understanding.asset_kind == "cinematic"
        and work.context.identity is not None
        and work.context.identity_path
        and work.context.identity_hash
        and work.context.portrait_path
        and work.context.portrait_hash
        and outfit is not None
        and scene is not None
        and action is not None
    ):
        source = CinematicSource(
            identity=work.context.identity,
            identity_path=work.context.identity_path,
            identity_hash=work.context.identity_hash,
            portrait_path=work.context.portrait_path,
            portrait_hash=work.context.portrait_hash,
            identity_reference_role=identity_reference_role,
            outfit=outfit,
            scene=scene,
            action=action,
            **common,
        )
    else:
        # 保留分析态，允许用户继续补充关联；仍然是同一份影像成品。
        source = AnalyzingVisualSource(
            **metadata,
            asset_kind=understanding.asset_kind,
            identity=work.context.identity,
            identity_path=work.context.identity_path,
            identity_hash=work.context.identity_hash,
            portrait_path=work.context.portrait_path,
            portrait_hash=work.context.portrait_hash,
            identity_reference_role=identity_reference_role,
            outfit=outfit,
            scene=scene,
            action=action,
            media_semantics=understanding.media_semantics,
            clip_kind=understanding.clip_kind,
            presentation_approved=bool(approved),
            analysis_job_id=work.id,
            identity_reason=understanding.identity_reason,
        )
    async with SESSION_LOCAL() as db:
        job = await db.scalar(
            select(VisualImportJob)
            .where(VisualImportJob.id == work.id, VisualImportJob.user_id == work.user_id)
            .with_for_update()
        )
        if job is None or job.state != "running" or job.context_json.get("execution_key") != work.context.execution_key:
            raise VisualStateError("分析任务已取消或更新")
        if any(entry.state in {"queued", "running"} for entry in import_operations(job.selections_json)):
            return False
        item = await visual_item(db, work.user_id, work.visual_id, lock=True)
        source.analysis_revision = job.revision
        if work.context.animated_image:
            source.original_duration_ms = work.duration_ms
        snapshot = work.snapshot.model_copy(update={"source": source, "review_reason": reason})
        asset = VisualAsset(
            media_kind=item.media_kind,
            media_path=item.media_path,
            poster_path=item.poster_path or "",
            width=item.width,
            height=item.height,
            duration_ms=item.duration_ms,
            visual_description=understanding.description,
            outfit_description=outfit.description if outfit else "",
        )
        if work.context.animated_image:
            asset.duration_ms = work.duration_ms
        if understanding.asset_kind == "actor":
            if work.context.actor_asset is None:
                raise VisualStateError("透明主体视频尚未完成文件和合成数据检查")
            asset = work.context.actor_asset.model_copy(
                update={
                    "visual_description": understanding.description,
                    "outfit_description": outfit.description if outfit else "",
                }
            )
        automatic_titles = {original.original_filename, original.original_filename[:160], ""}
        await publish_import_revision(
            db,
            work.user_id,
            item.id,
            work.expected_version,
            asset=asset,
            snapshot=snapshot,
            title=understanding.title if item.title in automatic_titles else item.title,
        )
        job.expected_version = work.expected_version + 1
        job.result_json = work.result.model_dump(mode="json")
        failed = any(entry.status == "failed" for entry in work.result.components)
        job.state, job.stage, job.error = (
            ("failed", "organize", "部分素材整理未完成，可重试；原件和已完成的结果已保存")
            if failed
            else ("paused", "confirmation", "有信息需要确认，其他素材已整理保存")
            if verdict == "review" or any(entry.status == "ambiguous" for entry in work.result.components)
            else ("succeeded", "complete", None)
        )
        await notify_visuals_updated(db, work.user_id)
        await db.commit()
        return True
