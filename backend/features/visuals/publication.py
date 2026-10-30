"""影像采纳的有界写入：身份、不可变版本、文件引用和任务结果同语句生效。"""

from dataclasses import dataclass
from typing import Any, Literal
from uuid import uuid4

from infrastructure.api_ids import ResourceId
from infrastructure.assets.asset_store import normalize_asset_reference, resolve_asset_reference
from infrastructure.text import utc_now
from pydantic import ConfigDict
from sqlalchemy import (
    BigInteger,
    ColumnElement,
    String,
    Table,
    column,
    func,
    literal,
    select,
    text,
    true,
    update,
    values,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.selectable import CTE

from features.assets.service import AssetReference, ensure_asset_references_in_session
from features.jobs.service import CompletedJob, completion_cte

from .models import VisualItem, VisualJob, VisualVersion, VisualVersionAsset
from .repository import VisualStateError
from .schemas import PresentedVisual, VisualAsset, VisualSnapshot


class PublishedVisual(PresentedVisual):
    model_config = ConfigDict(extra="forbid", from_attributes=True, frozen=True)
    current_version_id: ResourceId


@dataclass(frozen=True, slots=True)
class VisualPublication:
    visual: PublishedVisual
    image_id: int | None
    image_version_id: int | None
    completed: CompletedJob | None = None


@dataclass(frozen=True, slots=True)
class PublicationTarget:
    user_id: int
    outfit_id: int | None = None
    scene_id: int | None = None
    action_id: int | None = None
    target_visual_id: int | None = None
    expected_version: int | None = None
    title: str = ""


def _path(value: str | None) -> str | None:
    return normalize_asset_reference(value) if value else None


def _roles(asset: VisualAsset, snapshot: VisualSnapshot) -> dict[str, str]:
    source = snapshot.source
    paths = {
        "media": asset.media_path,
        "poster": asset.poster_path,
        "input": asset.input_image_path,
    }
    if asset.body is not None:
        paths |= {
            "mask": asset.body.mask_path,
            "first_frame": asset.body.first_frame_path,
            "last_frame": asset.body.last_frame_path,
        }
    if source.kind in ("actor", "cinematic"):
        paths |= {
            "identity": source.identity_path,
            "portrait": source.portrait_path,
            "outfit": source.outfit.path,
        }
    if source.kind in ("scene", "cinematic"):
        paths["scene"] = source.scene.path
    if source.kind == "analysis":
        paths |= {
            "identity": source.identity_path,
            "portrait": source.portrait_path,
            "outfit": source.outfit.path if source.outfit else None,
            "scene": source.scene.path if source.scene else None,
        }
    if source.original_path:
        paths["original"] = source.original_path
    return {role: path for role, value in paths.items() if (path := _path(value)) is not None}


def _literal(table: Table, name: str, value: Any) -> ColumnElement[Any]:
    return value if isinstance(value, ColumnElement) else literal(value, type_=table.c[name].type)


def _identity(table: Table) -> ColumnElement[int]:
    return func.nextval(func.pg_get_serial_sequence(table.name, "id")).cast(BigInteger)


def _source_ids(job: VisualJob | PublicationTarget) -> dict[str, int | None]:
    return {"outfit_id": job.outfit_id, "scene_id": job.scene_id, "action_id": job.action_id}


def _summary(asset: VisualAsset, snapshot: VisualSnapshot) -> dict[str, Any]:
    source = snapshot.source
    if source.kind == "analysis":
        clip_kind = source.clip_kind
        allowed_triggers = (
            list(source.action.allowed_triggers) if source.action and source.presentation_approved else []
        )
        if source.presentation_approved and asset.media_kind == "image":
            allowed_triggers = ["manual", "periodic"]
    elif source.kind == "scene":
        # 环境视频只有通过真实媒体检查后才发布；明确保存 loop，供环境目录
        # 与自动资格使用同一套媒体语义。环境图片仍是持续静态画面。
        clip_kind = "loop" if asset.media_kind == "video" else None
        allowed_triggers: list[str] = []
    else:
        clip_kind = source.action.clip_kind if source.action else None
        allowed_triggers = (
            list(source.action.allowed_triggers) if source.action and source.presentation_approved else []
        )
    return {
        "status": "ready",
        "description": asset.visual_description,
        "asset_kind": snapshot.kind,
        "clip_kind": clip_kind if asset.media_kind == "video" else None,
        "allowed_triggers": allowed_triggers,
    }


def _publish_entity(
    prefix: str,
    job: VisualJob | PublicationTarget,
    asset: VisualAsset,
    snapshot: VisualSnapshot,
    references: dict[str, AssetReference],
    *,
    regenerate: bool = False,
    input_item: CTE | None = None,
    input_version: CTE | None = None,
) -> tuple[CTE, CTE, CTE]:
    items, versions, attachments = VisualItem.__table__, VisualVersion.__table__, VisualVersionAsset.__table__
    roles = _roles(asset, snapshot)
    media = references[roles["media"]]
    allocation = select(
        (literal(job.target_visual_id, BigInteger) if regenerate else _identity(items)).label("item_id"),
        _identity(versions).label("version_id"),
    ).cte(f"{prefix}_ids")
    source = _source_ids(job)
    summary = {
        "current_version_id": allocation.c.version_id,
        **_summary(asset, snapshot),
        "content_hash": media.content_hash,
        **source,
    }
    if isinstance(job, PublicationTarget):
        summary["title"] = job.title
    if regenerate:
        if job.target_visual_id is None or job.expected_version is None:
            raise VisualStateError("重做任务缺少有效目标与版本")
        item_write = (
            update(items)
            .where(
                items.c.id == allocation.c.item_id,
                items.c.user_id == job.user_id,
                items.c.media_kind == asset.media_kind,
                *([] if isinstance(job, PublicationTarget) else [items.c.asset_kind == snapshot.kind]),
                items.c.current_version_id.in_(
                    select(versions.c.id).where(
                        versions.c.visual_id == job.target_visual_id,
                        versions.c.number == job.expected_version,
                    )
                ),
            )
            .values(**summary, updated_at=utc_now())
        )
    else:
        payload = {
            "id": allocation.c.item_id,
            "user_id": job.user_id,
            "source_key": uuid4().hex,
            "media_kind": asset.media_kind,
            "title": job.title if isinstance(job, PublicationTarget) else "",
            **summary,
        }
        item_write = insert(items).from_select(
            list(payload),
            select(*(_literal(items, name, value) for name, value in payload.items())),
            include_defaults=False,
        )
        if asset.media_kind == "image":
            item_write = item_write.on_conflict_do_update(
                index_elements=[items.c.user_id, items.c.content_hash],
                index_where=text("media_kind = 'image'"),
                set_={"content_hash": item_write.excluded.content_hash},
            )
    item = item_write.returning(*items.c).cte(f"{prefix}_item")
    input_path = _path(asset.input_image_path)
    body = asset.body if snapshot.kind == "actor" and asset.media_kind == "video" else None
    payload = {
        "id": item.c.current_version_id,
        "visual_id": item.c.id,
        "user_id": job.user_id,
        "number": job.expected_version + 1 if regenerate else 1,
        "description": asset.visual_description,
        "content_hash": media.content_hash,
        **source,
        "media_path": media.path,
        "poster_path": roles.get("poster"),
        "width": asset.width,
        "height": asset.height,
        "duration_ms": asset.duration_ms,
        "outfit_description": asset.outfit_description,
        "snapshot_json": snapshot.model_dump(mode="json"),
        "body_json": body.model_dump(mode="json") if body is not None else None,
        "input_image_id": input_item.c.id if input_item is not None else None,
        "input_image_version_id": input_version.c.id if input_version is not None else None,
        "input_image_path": input_path,
        "input_image_hash": references[input_path].content_hash if input_path else None,
    }
    selected_version = select(*(_literal(versions, name, value) for name, value in payload.items())).select_from(item)
    if input_item is not None and input_version is not None:
        selected_version = selected_version.join(input_item, true()).join(
            input_version, input_version.c.visual_id == input_item.c.id
        )
    version_write = insert(versions).from_select(list(payload), selected_version, include_defaults=False)
    if asset.media_kind == "image" and not regenerate:
        # UPSERT RETURNING 能读取并发获胜者的完整版本；普通SELECT仍使用语句开始时的快照。
        version_write = version_write.on_conflict_do_update(
            index_elements=[versions.c.id],
            set_={"id": version_write.excluded.id},
        )
    version = version_write.returning(*versions.c).cte(f"{prefix}_version")
    bindings = (
        values(
            column("role", String),
            column("asset_id", BigInteger),
            column("source_key", String),
        )
        .data([(role, references[path].id, uuid4().hex) for role, path in roles.items()])
        .alias(f"{prefix}_bindings")
    )
    selected = select(
        literal(job.user_id, BigInteger),
        version.c.id,
        bindings.c.asset_id,
        bindings.c.role,
        bindings.c.source_key,
    ).select_from(version.join(item, version.c.visual_id == item.c.id).join(bindings, true()))
    if not regenerate:
        selected = selected.join(allocation, item.c.id == allocation.c.item_id)
    linked = (
        insert(attachments)
        .from_select(
            ["user_id", "version_id", "asset_id", "role", "source_key"],
            selected,
            include_defaults=False,
        )
        .on_conflict_do_nothing(constraint="uq_visual_version_asset_role")
        .cte(f"{prefix}_assets")
    )
    return item, version, linked


async def _publish(
    db: AsyncSession,
    user_id: int,
    job: VisualJob,
    asset: VisualAsset,
    *,
    snapshot: VisualSnapshot,
    role: Literal["opening", "result"],
    checkpoint: dict | None = None,
) -> VisualPublication:
    if job.user_id != user_id:
        raise VisualStateError("制作任务不属于当前账户")
    if snapshot.kind != job.asset_kind:
        raise VisualStateError("冻结输入与制作目标类型不一致")
    if snapshot.kind == "actor" and (
        asset.media_kind != "video"
        or asset.body is None
        or not asset.body.mask_path
        or not asset.body.first_frame_path
        or not asset.body.last_frame_path
    ):
        raise VisualStateError("主体动画必须包含透明视频、逐帧遮罩和首末帧")
    # 已发布的起始图沿用确认时的版本，图片重做不能改变视频的真实输入。
    existing_image: VisualVersion | None = None
    if (
        role == "result"
        and asset.media_kind == "video"
        and snapshot.kind != "actor"
        and job.result_image_id is not None
        and job.result_image_version_id is not None
    ):
        existing_image = await db.scalar(
            select(VisualVersion)
            .join(VisualItem, VisualItem.id == VisualVersion.visual_id)
            .where(
                VisualItem.user_id == user_id,
                VisualItem.id == job.result_image_id,
                VisualVersion.id == job.result_image_version_id,
                VisualVersion.media_path == _path(asset.input_image_path),
                VisualItem.media_kind == "image",
            )
        )

    roles = _roles(asset, snapshot)
    references = await ensure_asset_references_in_session(db, user_id, set(roles.values()))
    media = references[roles["media"]]
    if asset.media_kind == "image" and (media.width != asset.width or media.height != asset.height):
        raise VisualStateError("成品尺寸与实际图片不一致，本次结果未采纳")
    image_item = image_version = image_links = None
    if asset.media_kind == "video" and snapshot.kind != "actor":
        input_path = roles.get("input")
        if input_path is None:
            raise VisualStateError("视频缺少已确认的起始画面")
        image = references[input_path]
        if image.width is None or image.height is None:
            raise VisualStateError("起始画面的尺寸记录缺失")
        if existing_image is not None:
            items, versions = VisualItem.__table__, VisualVersion.__table__
            image_item = select(items.c.id).where(items.c.id == existing_image.visual_id).cte("opening_existing_item")
            image_version = (
                select(versions.c.id, versions.c.visual_id)
                .where(versions.c.id == existing_image.id, versions.c.visual_id == existing_image.visual_id)
                .cte("opening_existing_version")
            )
        else:
            opening = VisualAsset(
                media_kind="image",
                media_path=input_path,
                width=image.width,
                height=image.height,
                visual_description=asset.visual_description,
                outfit_description=asset.outfit_description,
            )
            image_item, image_version, image_links = _publish_entity("opening", job, opening, snapshot, references)
    item, version, links = _publish_entity(
        "published",
        job,
        asset,
        snapshot,
        references,
        regenerate=role == "result" and job.purpose == "regenerate",
        input_item=image_item,
        input_version=image_version,
    )
    details = VisualJob.__table__
    pointers = (
        {"result_image_id": item.c.id, "result_image_version_id": version.c.id}
        if role == "opening"
        else {
            "result_visual_id": item.c.id,
            "result_version_id": version.c.id,
            "result_image_id": version.c.input_image_id,
            "result_image_version_id": version.c.input_image_version_id,
        }
    )
    result = (
        update(details)
        .where(details.c.id == job.id, details.c.owner_id == item.c.user_id, version.c.visual_id == item.c.id)
        .values(
            **pointers,
        )
        .returning(details.c.result_image_id, details.c.result_image_version_id)
        .cte("job_result")
    )
    statement = (
        select(
            item.c.id,
            item.c.user_id,
            item.c.media_kind,
            item.c.asset_kind,
            item.c.title,
            item.c.status,
            item.c.created_at,
            item.c.updated_at,
            version.c.id.label("current_version_id"),
            version.c.number.label("version"),
            version.c.media_path,
            version.c.poster_path,
            version.c.width,
            version.c.height,
            version.c.duration_ms,
            version.c.content_hash,
            version.c.description,
            version.c.outfit_description,
            version.c.snapshot_json,
            version.c.outfit_id,
            version.c.scene_id,
            version.c.action_id,
            version.c.input_image_id,
            version.c.input_image_version_id,
            version.c.input_image_path,
            version.c.input_image_hash,
            result.c.result_image_id,
            result.c.result_image_version_id,
        )
        .select_from(item.join(version, version.c.visual_id == item.c.id).join(result, true()))
        .add_cte(links)
    )
    if image_links is not None:
        statement = statement.add_cte(image_links)
    if role == "result":
        if checkpoint is None:
            raise VisualStateError("采纳缺少完整的制作检查点")
        completed = completion_cte(user_id, job.id, job.revision, checkpoint)
        statement = statement.join(completed, completed.c.job_id == job.id).add_columns(
            completed.c.revision.label("completed_revision"),
            completed.c.finished_at.label("completed_at"),
            completed.c.updated_at.label("completed_updated_at"),
        )
    row = (await db.execute(statement)).mappings().one_or_none()
    if row is None:
        raise VisualStateError("影像版本已变化，本次结果未替换")
    payload = dict(row)
    image_id = payload.pop("result_image_id")
    image_version_id = payload.pop("result_image_version_id")
    completion = (
        CompletedJob(
            payload.pop("completed_revision"), payload.pop("completed_at"), payload.pop("completed_updated_at")
        )
        if role == "result"
        else None
    )
    published = PublishedVisual.model_validate(payload)
    if resolve_asset_reference(published.media_path) is None:
        raise VisualStateError("同内容影像的原文件缺失，本次结果未采纳")
    return VisualPublication(published, image_id, image_version_id, completion)


async def publish_visual_result(
    db: AsyncSession,
    user_id: int,
    job: VisualJob,
    asset: VisualAsset,
    *,
    snapshot: VisualSnapshot,
    checkpoint: dict,
) -> VisualPublication:
    return await _publish(db, user_id, job, asset, snapshot=snapshot, role="result", checkpoint=checkpoint)


async def publish_imported_visual(
    db: AsyncSession,
    user_id: int,
    asset: VisualAsset,
    snapshot: VisualSnapshot,
    *,
    title: str,
) -> int:
    """原件经确定性检查后直接发布；不制造生成任务或视频起始图成品。"""

    if snapshot.source.original_path is None or snapshot.source.original_content_hash is None:
        raise VisualStateError("上传发布缺少原件来源")
    roles = _roles(asset, snapshot)
    references = await ensure_asset_references_in_session(db, user_id, set(roles.values()))
    if references[roles["media"]].content_hash != snapshot.source.original_content_hash:
        raise VisualStateError("上传原件校验失败")
    item, version, links = _publish_entity(
        "imported", PublicationTarget(user_id=user_id, title=title), asset, snapshot, references
    )
    return (
        await db.execute(select(item.c.id).join(version, version.c.visual_id == item.c.id).add_cte(links))
    ).scalar_one()


async def publish_import_revision(
    db: AsyncSession,
    user_id: int,
    visual_id: int,
    expected_version: int,
    *,
    asset: VisualAsset,
    snapshot: VisualSnapshot,
    title: str,
) -> int:
    if snapshot.source.original_path is None or snapshot.source.original_content_hash is None:
        raise VisualStateError("分析结果缺少上传来源")
    roles = _roles(asset, snapshot)
    references = await ensure_asset_references_in_session(db, user_id, set(roles.values()))
    source = snapshot.source
    target = PublicationTarget(
        user_id=user_id,
        target_visual_id=visual_id,
        expected_version=expected_version,
        title=title,
        outfit_id=source.outfit.material_id if getattr(source, "outfit", None) else None,
        scene_id=source.scene.scene_id if getattr(source, "scene", None) else None,
        action_id=source.action.action_id if getattr(source, "action", None) else None,
    )
    item, version, links = _publish_entity("analysis", target, asset, snapshot, references, regenerate=True)
    result = await db.scalar(select(version.c.id).join(item, version.c.visual_id == item.c.id).add_cte(links))
    if result is None:
        raise VisualStateError("影像版本已更新，分析结果尚未替换旧版本")
    return result


async def publish_opening_result(
    db: AsyncSession,
    user_id: int,
    job: VisualJob,
    asset: VisualAsset,
    *,
    snapshot: VisualSnapshot,
) -> PublishedVisual:
    if asset.media_kind != "image":
        raise VisualStateError("起始画面必须是图片")
    result = await _publish(
        db, user_id, job, asset.model_copy(update={"input_image_path": None}), snapshot=snapshot, role="opening"
    )
    return result.visual
