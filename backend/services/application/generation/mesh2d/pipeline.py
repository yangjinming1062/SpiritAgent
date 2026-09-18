"""2D 完整资产编排：分层立绘与姿态包生成、发布、换装及就绪事件。"""

import asyncio
import base64
import hashlib
import json

from components import SESSION_LOCAL, SETTINGS, get_logger, track_user_task
from modules.companion import AvatarAsset, Companion2DModel, CompanionOutfit
from modules.ws import emit_ws_event
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from services.infrastructure.assets import asset_store
from services.infrastructure.seethrough import split_to_psd

from ..avatar_service import get_avatar_job_lock, load_avatar_bytes_as_data_uri, normalize_avatar_url_to_bare
from .poses import Side, compose_single_pose_from_image, generate_pose_pack, generate_single_pose
from .priority_queue import get_default_queue

logger = get_logger(__name__)


class Mesh2DPipelineError(RuntimeError):
    """2d 流水线失败 — 调用方应继续走程序化蛋兜底。"""


# 后台提交任务的强引用集合 + 在飞 model_id 集合（后者供 service 侧识别重启遗留的僵尸 generating 行）
_PIPELINE_TASKS: set[asyncio.Task[None]] = set()
_ACTIVE_MODEL_IDS: set[int] = set()
# 在飞单侧姿态重生成 (user_id, outfit_id)；仅内存标记，进程重启即丢（客户端有兜底超时）
_ACTIVE_POSE_OUTFITS: set[tuple[int, int]] = set()


def active_model_ids() -> frozenset[int]:
    return frozenset(_ACTIVE_MODEL_IDS)


def pose_regeneration_in_progress(user_id: int, outfit_id: int) -> bool:
    return (user_id, outfit_id) in _ACTIVE_POSE_OUTFITS


async def _safe_load_avatar_bytes(url: str) -> bytes:
    data_uri = await asyncio.to_thread(load_avatar_bytes_as_data_uri, url)

    if not data_uri or not data_uri.startswith("data:"):
        raise Mesh2DPipelineError(f"failed to read avatar bytes for url: {url}")

    _, b64 = data_uri.split(",", 1)
    return await asyncio.to_thread(base64.b64decode, b64)


async def _generate(
    user_id: int,
    fullbody_bytes: bytes,
) -> tuple[str, list[dict[str, str]]]:
    paths: list[str] = []
    try:
        async with asyncio.timeout(SETTINGS.seethrough_total_budget_seconds), asyncio.TaskGroup() as tasks:
            psd_task = tasks.create_task(split_to_psd(fullbody_bytes))
            poses_task = tasks.create_task(generate_pose_pack(fullbody_bytes, user_id))
        poses, textures = poses_task.result()
        psd_path = await asset_store.save_companion_asset_async(
            psd_task.result(),
            user_id=user_id,
            label="2d_psd",
            ext="psd",
        )
        paths.append(psd_path)
        layers = [{"name": "psd", "url": psd_path}]
        for name, data in textures.items():
            path = await asset_store.save_companion_asset_async(
                data,
                user_id=user_id,
                label=name,
                ext="webp",
            )
            paths.append(path)
            layers.append({"name": name, "url": path})
        manifest = {
            "schema": "spiritagent.2d.psd/1",
            "kind": "psd",
            "psd": psd_path,
            "poses": poses.model_dump(by_alias=True),
        }
        return json.dumps(manifest, ensure_ascii=False), layers
    except (Exception, asyncio.CancelledError) as exc:
        for path in paths:
            await asyncio.to_thread(asset_store.unlink_companion_asset, path)
        if isinstance(exc, asyncio.CancelledError):
            raise
        logger.warning("2d asset generation failed", exc_info=True, extra={"user_id": user_id})
        raise Mesh2DPipelineError("2D 资产生成失败，请重试") from exc


def run_mesh2d_pipeline(
    *,
    user_id: int,
    model_id: int,
    fullbody_url: str,
    priority: str = "high",
) -> asyncio.Task[None]:
    """提交异步流水线并立即返回；状态写入 Companion2DModel 表，由 WS 事件驱动前端刷新。

    队列 worker 内各阶段自开短会话——请求路径毫秒级返回（202 语义），不与
    视觉 LLM 往返共享请求会话。"""
    queue = get_default_queue()

    async def _fetch_model(db: AsyncSession) -> Companion2DModel | None:
        return (
            await db.execute(
                select(Companion2DModel).where(
                    Companion2DModel.id == model_id,
                    Companion2DModel.user_id == user_id,
                ),
            )
        ).scalar_one_or_none()

    async def _task() -> None:
        async with SESSION_LOCAL() as db:
            model = await _fetch_model(db)

            if model is None:
                logger.warning(
                    "2d model row vanished before pipeline run",
                    extra={"model_id": model_id, "user_id": user_id},
                )
                return

            model.status = "generating"
            model.error = None
            await db.commit()

        try:
            normalized_url = normalize_avatar_url_to_bare(fullbody_url) or fullbody_url
            fullbody_bytes = await _safe_load_avatar_bytes(normalized_url)
            manifest_json, layer_entries = await _generate(user_id, fullbody_bytes)
        except Mesh2DPipelineError as exc:
            logger.warning(
                "2d pipeline failed",
                extra={"user_id": user_id, "model_id": model_id, "error": str(exc)},
            )
            await _mark_failed(user_id=user_id, model_id=model_id, error=str(exc), reason=str(exc))
            return
        except Exception as exc:
            logger.exception(
                "2d pipeline crashed",
                extra={"user_id": user_id, "model_id": model_id},
            )
            error = f"unexpected: {exc!s}"
            await _mark_failed(user_id=user_id, model_id=model_id, error=error, reason=error)
            return

        manifest_path: str | None = None
        try:
            async with get_avatar_job_lock(user_id), SESSION_LOCAL() as db:
                model = await _fetch_model(db)
                if model is None:
                    logger.warning(
                        "2d model row vanished after pipeline run",
                        extra={"model_id": model_id, "user_id": user_id},
                    )
                    for entry in layer_entries:
                        asset_store.unlink_companion_asset(entry["url"])
                    return
                model.status = "succeeded"
                model.manifest_json = manifest_json
                model.content_hash = hashlib.sha256(manifest_json.encode("utf-8")).hexdigest()
                model.layers_json = json.dumps(layer_entries, ensure_ascii=False)

                manifest_path = asset_store.save_companion_asset(
                    manifest_json.encode("utf-8"),
                    user_id=user_id,
                    label=f"2d_manifest_{model_id}",
                    ext="json",
                )
                model.manifest_path = manifest_path
                # outfit 成功接缝：置 ready；自动穿着标记仍真则原子翻转穿着（先停用后激活——
                # 部分唯一索引不可延迟）。标记已被手动穿着清掉时只入柜不换装。
                event_outfit_id = model.outfit_id
                worn: bool | None = None
                event_outfit_name = ""
                avatar_active = await db.scalar(
                    select(AvatarAsset.active).where(AvatarAsset.id == model.avatar_id, AvatarAsset.user_id == user_id),
                )
                if event_outfit_id is not None:
                    # 锁内读取标记并翻转——与手动穿着路径互斥，否则「读到标记为真 → 用户手选
                    # 提交 → 切分完成覆盖手选」的竞态会击穿两段式激活的用户选择保证
                    outfit = (
                        await db.execute(
                            select(CompanionOutfit).where(
                                CompanionOutfit.id == event_outfit_id,
                                CompanionOutfit.user_id == user_id,
                            ),
                        )
                    ).scalar_one_or_none()
                    if outfit is not None:
                        outfit.status = "ready"
                        event_outfit_name = outfit.name
                        if outfit.pending_wear and avatar_active:
                            await db.execute(
                                update(Companion2DModel)
                                .where(
                                    Companion2DModel.user_id == user_id,
                                    Companion2DModel.active.is_(True),
                                    Companion2DModel.id != model_id,
                                )
                                .values(active=False)
                                .execution_options(synchronize_session=False),
                            )
                            await db.execute(
                                update(CompanionOutfit)
                                .where(CompanionOutfit.user_id == user_id, CompanionOutfit.active.is_(True))
                                .values(active=False)
                                .execution_options(synchronize_session=False),
                            )
                            model.active = True
                            outfit.active = True
                            outfit.pending_wear = False
                            worn = True
                        else:
                            model.active = False
                            outfit.pending_wear = False
                            worn = False
                    else:
                        # outfit 行已被删除的防御分支：不可激活（现有穿着行仍激活），留孤儿行
                        model.active = False
                else:
                    latest_id = await db.scalar(
                        select(Companion2DModel.id)
                        .where(
                            Companion2DModel.user_id == user_id,
                            Companion2DModel.avatar_id == model.avatar_id,
                            Companion2DModel.outfit_id.is_(None),
                        )
                        .order_by(Companion2DModel.id.desc())
                        .limit(1),
                    )
                    worn_outfit = await db.scalar(
                        select(Companion2DModel.id).where(
                            Companion2DModel.user_id == user_id,
                            Companion2DModel.avatar_id == model.avatar_id,
                            Companion2DModel.active.is_(True),
                            Companion2DModel.outfit_id.is_not(None),
                        ),
                    )
                    model.active = False
                    if avatar_active and latest_id == model_id and worn_outfit is None:
                        # 完整资产发布前保留当前形象；切换时保证只有一条激活行。
                        await db.execute(
                            update(Companion2DModel)
                            .where(
                                Companion2DModel.user_id == user_id,
                                Companion2DModel.active.is_(True),
                                Companion2DModel.id != model_id,
                            )
                            .values(active=False)
                            .execution_options(synchronize_session=False),
                        )
                        await db.execute(
                            update(CompanionOutfit)
                            .where(CompanionOutfit.user_id == user_id, CompanionOutfit.active.is_(True))
                            .values(active=False),
                        )
                        model.active = True
                await db.commit()
        except (Exception, asyncio.CancelledError) as exc:
            for entry in layer_entries:
                asset_store.unlink_companion_asset(entry["url"])
            if manifest_path:
                asset_store.unlink_companion_asset(manifest_path)
            if isinstance(exc, asyncio.CancelledError):
                raise
            logger.exception("2d asset publication failed", extra={"model_id": model_id, "user_id": user_id})
            await _mark_failed(
                user_id=user_id,
                model_id=model_id,
                error="2D 资产发布失败，请重试",
                reason="2D 资产发布失败，请重试",
            )
            return

        manifest_url = asset_store.signed_companion_asset_url(manifest_path)
        await _emit_mesh2d_ready(user_id, model_id, manifest_url, layer_entries)
        if worn is not None:
            payload: dict = {"outfit_id": event_outfit_id, "worn": worn}
            if event_outfit_name:
                payload["name"] = event_outfit_name
            await _emit_outfit_event(user_id, "companion.outfit.updated", payload)

    async def _submit() -> None:
        _ACTIVE_MODEL_IDS.add(model_id)
        try:
            await queue.submit(f"2d:{user_id}:{model_id}", _task, priority=priority)
        finally:
            _ACTIVE_MODEL_IDS.discard(model_id)

    wrapper = asyncio.create_task(_submit(), name=f"companion.2d.{user_id}.{model_id}")
    track_user_task(user_id, wrapper, cancel_on_maintenance=False)

    def _done(task: asyncio.Task[None]) -> None:
        _PIPELINE_TASKS.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.error("2d pipeline submission failed", exc_info=task.exception())

    _PIPELINE_TASKS.add(wrapper)
    wrapper.add_done_callback(_done)
    return wrapper


async def _latest_succeeded_model(db: AsyncSession, user_id: int, outfit_id: int) -> Companion2DModel | None:
    return (
        await db.execute(
            select(Companion2DModel)
            .where(
                Companion2DModel.user_id == user_id,
                Companion2DModel.outfit_id == outfit_id,
                Companion2DModel.status == "succeeded",
            )
            .order_by(Companion2DModel.id.desc())
            .limit(1),
        )
    ).scalar_one_or_none()


def run_pose_side_regeneration(
    *,
    user_id: int,
    outfit_id: int,
    side: Side,
    user_image: bytes | None = None,
) -> asyncio.Task[None]:
    """提交单侧扶边姿态重生成并立即返回。成功原位替换该侧两张姿态纹理与 manifest 的
    poses 子树并重算 content_hash，其余资产不动；失败保留旧姿态、外观保持 ready——
    单侧重生成是优化而非重建，失败不得波及整套资产。任务不落库，进程重启即丢。
    user_image 提供时为自备图采纳：跳过主姿态图生图，用户图直接进入既有后处理。"""
    queue = get_default_queue()

    async def _task() -> None:
        layer_entries: list[dict[str, str]] = []
        try:
            async with SESSION_LOCAL() as db:
                outfit = (
                    await db.execute(
                        select(CompanionOutfit).where(
                            CompanionOutfit.id == outfit_id,
                            CompanionOutfit.user_id == user_id,
                        ),
                    )
                ).scalar_one_or_none()
                model = await _latest_succeeded_model(db, user_id, outfit_id) if outfit is not None else None
                if outfit is None or model is None:
                    logger.warning(
                        "pose side regen target vanished",
                        extra={"user_id": user_id, "outfit_id": outfit_id},
                    )
                    return
                model_id = model.id
                fullbody_url = normalize_avatar_url_to_bare(outfit.fullbody_url) or outfit.fullbody_url

            if user_image is not None:
                pose, textures = await compose_single_pose_from_image(user_image, user_id, side)
            else:
                try:
                    fullbody_bytes = await _safe_load_avatar_bytes(fullbody_url)
                except Mesh2DPipelineError as exc:
                    raise Mesh2DPipelineError("外观立绘已不可读，无法重新生成姿态") from exc
                pose, textures = await generate_single_pose(fullbody_bytes, user_id, side)
            for name, data in textures.items():
                layer_entries.append(
                    {
                        "name": name,
                        "url": await asset_store.save_companion_asset_async(
                            data,
                            user_id=user_id,
                            label=name,
                            ext="webp",
                        ),
                    },
                )
        except (Exception, asyncio.CancelledError) as exc:
            for entry in layer_entries:
                asset_store.unlink_companion_asset(entry["url"])
            if isinstance(exc, asyncio.CancelledError):
                raise
            logger.warning(
                "pose side regeneration failed",
                exc_info=True,
                extra={"user_id": user_id, "outfit_id": outfit_id, "side": side},
            )
            await _emit_outfit_event(
                user_id,
                "companion.outfit.failed",
                {"outfit_id": outfit_id, "reason": str(exc) or "扶边姿态重新生成失败，请重试", "side": side},
            )
            return

        manifest_path: str | None = None
        replaced_urls: list[str] = []
        old_manifest_path: str | None = None
        layers: list[dict[str, str]] = []
        try:
            async with get_avatar_job_lock(user_id), SESSION_LOCAL() as db:
                model = (
                    await db.execute(
                        select(Companion2DModel).where(
                            Companion2DModel.id == model_id,
                            Companion2DModel.user_id == user_id,
                        ),
                    )
                ).scalar_one_or_none()
                latest = await _latest_succeeded_model(db, user_id, outfit_id)
                if model is None or latest is None or latest.id != model_id:
                    # 资产行被删或整包重切分已换代：本次结果作废
                    logger.info(
                        "pose side regen superseded, discarding",
                        extra={"user_id": user_id, "outfit_id": outfit_id, "model_id": model_id},
                    )
                    for entry in layer_entries:
                        asset_store.unlink_companion_asset(entry["url"])
                    return
                try:
                    manifest = json.loads(model.manifest_json)
                except json.JSONDecodeError as exc:
                    raise Mesh2DPipelineError("外观资产数据损坏，无法重新生成姿态") from exc
                poses = manifest.get("poses")
                if not isinstance(poses, dict) or side not in poses:
                    raise Mesh2DPipelineError("该外观资产缺少贴边姿态数据，无法单侧重生成")
                old_manifest_path = model.manifest_path or None
                layers = json.loads(model.layers_json or "[]")
                new_url_by_name = {entry["name"]: entry["url"] for entry in layer_entries}
                replaced_urls = [
                    entry["url"]
                    for entry in layers
                    if isinstance(entry, dict)
                    and entry.get("name") in new_url_by_name
                    and isinstance(entry.get("url"), str)
                    and entry["url"]
                ]
                for entry in layers:
                    if isinstance(entry, dict) and entry.get("name") in new_url_by_name:
                        entry["url"] = new_url_by_name[entry["name"]]
                manifest["poses"][side] = pose.model_dump()
                manifest_json = json.dumps(manifest, ensure_ascii=False)
                model.manifest_json = manifest_json
                model.content_hash = hashlib.sha256(manifest_json.encode("utf-8")).hexdigest()
                model.layers_json = json.dumps(layers, ensure_ascii=False)
                manifest_path = asset_store.save_companion_asset(
                    manifest_json.encode("utf-8"),
                    user_id=user_id,
                    label=f"2d_manifest_{model_id}",
                    ext="json",
                )
                model.manifest_path = manifest_path
                await db.commit()
        except (Exception, asyncio.CancelledError) as exc:
            for entry in layer_entries:
                asset_store.unlink_companion_asset(entry["url"])
            if manifest_path:
                asset_store.unlink_companion_asset(manifest_path)
            if isinstance(exc, asyncio.CancelledError):
                raise
            logger.exception("pose side publication failed", extra={"user_id": user_id, "outfit_id": outfit_id})
            await _emit_outfit_event(
                user_id,
                "companion.outfit.failed",
                {"outfit_id": outfit_id, "reason": str(exc) or "扶边姿态重新生成失败，请重试", "side": side},
            )
            return

        # 提交成功后才清理被替换的旧资产；中断窗口最多留孤儿文件，不破坏现行资产
        if old_manifest_path:
            asset_store.unlink_companion_asset(old_manifest_path)
        for url in replaced_urls:
            asset_store.unlink_companion_asset(url)

        manifest_url = asset_store.signed_companion_asset_url(manifest_path)
        await _emit_mesh2d_ready(user_id, model_id, manifest_url, layers)
        await _emit_outfit_event(
            user_id,
            "companion.outfit.updated",
            {"outfit_id": outfit_id, "worn": False},
        )

    async def _submit() -> None:
        try:
            await queue.submit(f"pose:{user_id}:{outfit_id}", _task, priority="high")
        finally:
            _ACTIVE_POSE_OUTFITS.discard((user_id, outfit_id))

    _ACTIVE_POSE_OUTFITS.add((user_id, outfit_id))
    wrapper = asyncio.create_task(_submit(), name=f"companion.pose.{user_id}.{outfit_id}.{side}")
    track_user_task(user_id, wrapper, cancel_on_maintenance=False)
    return wrapper


async def _mark_failed(*, user_id: int, model_id: int, error: str, reason: str) -> None:
    """失败态落库 + 事件下发；自身异常只记日志（失败路径不能再抛）。"""
    try:
        outfit_failed_id: int | None = None
        async with SESSION_LOCAL() as db:
            model = (
                await db.execute(
                    select(Companion2DModel).where(
                        Companion2DModel.id == model_id,
                        Companion2DModel.user_id == user_id,
                    ),
                )
            ).scalar_one_or_none()
            if model is not None:
                model.status = "failed"
                model.error = error
                if model.outfit_id is not None:
                    outfit = (
                        await db.execute(
                            select(CompanionOutfit).where(
                                CompanionOutfit.id == model.outfit_id,
                                CompanionOutfit.user_id == user_id,
                            ),
                        )
                    ).scalar_one_or_none()
                    if outfit is not None and outfit.status == "splitting":
                        outfit.status = "failed"
                        outfit.pending_wear = False
                        outfit_failed_id = outfit.id
                await db.commit()
        await _emit_mesh2d_failed(user_id, model_id, reason=reason)
        if outfit_failed_id is not None:
            await _emit_outfit_event(
                user_id,
                "companion.outfit.failed",
                {"outfit_id": outfit_failed_id, "reason": reason},
            )
    except Exception:
        logger.warning("2d failed-state persistence error", exc_info=True)


async def _emit_outfit_event(user_id: int, event_type: str, payload: dict) -> None:
    try:
        async with SESSION_LOCAL() as db:
            emit_ws_event(db, user_id=user_id, event_type=event_type, payload=payload)
            await db.commit()
    except Exception:
        logger.warning("Failed to emit %s", event_type, exc_info=True)


async def _emit_mesh2d_ready(
    user_id: int,
    model_id: int,
    manifest_url: str | None,
    layer_entries: list[dict[str, str]],
) -> None:
    """通过 ws_events 表写入一条 WS 事件；事件推送由 chat ws 通道消费。"""
    try:
        payload = {
            "model_id": model_id,
            "manifest_url": manifest_url,
            "layers": layer_entries,
        }
        async with SESSION_LOCAL() as db:
            emit_ws_event(db, user_id=user_id, event_type="companion.2d.ready", payload=payload)
            await db.commit()
    except Exception:
        logger.warning("Failed to emit companion.2d.ready", exc_info=True)


async def _emit_mesh2d_failed(
    user_id: int,
    model_id: int,
    *,
    reason: str,
) -> None:
    try:
        payload = {"model_id": model_id, "reason": reason}
        async with SESSION_LOCAL() as db:
            emit_ws_event(db, user_id=user_id, event_type="companion.2d.failed", payload=payload)
            await db.commit()
    except Exception:
        logger.warning("Failed to emit companion.2d.failed", exc_info=True)
