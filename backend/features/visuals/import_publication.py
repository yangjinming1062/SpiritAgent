"""分析结果追加不可变版本，并在同一事务完成分析任务。"""

from infrastructure.database import SESSION_LOCAL
from sqlalchemy import select

from .import_jobs import ImportCatalogCandidate, ImportWork
from .models import VisualImportJob
from .publication import publish_import_revision
from .repository import VisualStateError, notify_visuals_updated, visual_item
from .schemas import ActorClipSource, AnalyzingVisualSource, CinematicSource, SceneMediaSource, VisualAsset


async def complete_import_analysis(
    work: ImportWork, selected: list[ImportCatalogCandidate], reviewed: bool, reason: str
) -> None:
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
    unresolved = any(entry.status in {"failed", "reviewing", "ambiguous"} for entry in work.result.components)
    approved = reviewed and understanding.presentation_verdict == "pass" and not unresolved
    if work.context.animated_image:
        approved = False
        reason = "动图原件已保留；当前在影像库中预览，不参与舞台自动呈现。" + reason
    if understanding.asset_kind == "scene":
        # 环境影像只有在冻结了可复用的环境素材后才能进入自动目录；
        # 仅能描述环境但没有完整场景引用的原件仍可收藏和手动预览。
        approved = approved and understanding.identity_match == "absent" and scene is not None
    else:
        approved = (
            approved
            and understanding.identity_match == "same"
            and work.context.identity is not None
            and outfit is not None
        )
        if understanding.asset_kind == "cinematic":
            approved = approved and scene is not None
        approved = (
            approved and understanding.media_semantics is not None and understanding.media_semantics.auto_selectable
        )
    if work.media_kind == "video":
        approved = (
            approved and understanding.media_semantics is not None and understanding.media_semantics.auto_selectable
        )
        if understanding.asset_kind in {"cinematic", "actor"}:
            approved = approved and action is not None and understanding.clip_kind == action.clip_kind
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
        "clip_kind": understanding.clip_kind,
    }
    if understanding.asset_kind == "scene" and scene is not None:
        source = SceneMediaSource(
            scene=scene,
            **{key: value for key, value in common.items() if key not in {"media_semantics", "clip_kind"}},
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
            identity_reference_role=original.identity_reference_role,
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
            identity_reference_role=original.identity_reference_role,
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
            identity_reference_role=original.identity_reference_role,
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
        job.selections_json = []
        failed = any(entry.status == "failed" for entry in work.result.components)
        job.state, job.stage, job.error = (
            ("failed", "organize", "部分素材整理未完成，可重试；原件和已完成的结果已保存")
            if failed
            else ("succeeded", "complete", None)
        )
        await notify_visuals_updated(db, work.user_id)
        await db.commit()
