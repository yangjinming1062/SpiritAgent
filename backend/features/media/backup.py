"""音色方案的备份与恢复贡献。"""

from typing import Any
from uuid import UUID

from infrastructure.assets.paths import parse_companion_asset_path
from infrastructure.backup import BackupContext, BackupImportMode, BackupTable
from sqlalchemy import BigInteger, column, select, update, values
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from features.jobs.service import rewrite_job_checkpoints

from .chain import MediaChainState
from .identity_contracts import VisualIdentitySnapshot
from .models import ImageGenJob, MediaContinuation, MediaManualOperation, MediaReviewAttempt, VideoGenJob
from .review_views import ImageJobParams
from .video_jobs import _VideoJobParams
from .voice_models import CustomVoice


def _prepare(raw: dict[str, Any], payload: dict[str, Any], context: BackupContext) -> None:
    if payload["kind"] not in {"sample", "design", "clone"}:
        raise ValueError("Invalid voice source")
    if UUID(payload["token"]).hex != payload["token"]:
        raise ValueError("Invalid voice reference")
    if payload["kind"] != "sample":
        UUID(payload["model_card_id"])
        if payload["provider"] not in {"mimo", "minimax"} or not payload["model"]:
            raise ValueError("Invalid voice provider")
    for path in (payload.get("reference_path"), payload.get("preview_path")):
        if path:
            parsed = parse_companion_asset_path(path)
            if parsed is None or parsed[0] != context.asset_owner_id:
                raise ValueError("Voice sample belongs to another account")
    for path_key, asset_key in (("reference_path", "reference_asset_id"), ("preview_path", "preview_asset_id")):
        if bool(payload.get(path_key)) != (payload.get(asset_key) is not None):
            raise ValueError("Voice file and asset reference must be saved together")
    payload["is_current"] = False


async def _retain_versions(db: AsyncSession, user_id: int) -> None:
    # 旧消息和未恢复的设置仍可引用不可变版本；覆盖恢复只交接当前槽位。
    return None


async def _existing_version(
    db: AsyncSession,
    raw: dict[str, Any],
    payload: dict[str, Any],
    context: BackupContext,
) -> int | None:
    row = await db.scalar(
        select(CustomVoice).where(CustomVoice.user_id == context.user_id, CustomVoice.token == payload["token"]),
    )
    if row is None:
        return None
    for field in ("kind", "provider", "model", "model_card_id", "provider_voice_id", "prompt"):
        if getattr(row, field) != payload[field]:
            raise ValueError("音色引用与已有方案不一致")
    if row.version == 0 and payload["version"] > 0:
        row.version = payload["version"]
        row.expires_at = None
    if (payload.get("reference_path") and not row.reference_path) or (
        payload.get("preview_path") and not row.preview_path
    ):
        if not row.reference_path and payload.get("reference_path"):
            row.reference_path = payload["reference_path"]
            row.reference_asset_id = payload["reference_asset_id"]
        if not row.preview_path and payload.get("preview_path"):
            row.preview_path = payload["preview_path"]
            row.preview_asset_id = payload["preview_asset_id"]
        context.rewriter.inserted_rows.add(("custom_voices", str(raw["id"])))
    return row.id


async def restore_current_voices(
    db: AsyncSession,
    user_id: int,
    rows: list[dict[str, Any]],
    id_map: dict[str, int | str],
    *,
    mode: BackupImportMode,
) -> None:
    """用户设置成功恢复后交接当前方案；仅导入历史版本不改变当前槽位。"""
    if mode == "overwrite":
        await db.execute(update(CustomVoice).where(CustomVoice.user_id == user_id).values(is_current=False))
        await db.flush()
    for kind in ("design", "clone"):
        if await db.scalar(
            select(CustomVoice.id).where(
                CustomVoice.user_id == user_id,
                CustomVoice.kind == kind,
                CustomVoice.is_current.is_(True),
            ),
        ):
            continue
        imported = [id_map[str(row["id"])] for row in rows if row.get("kind") == kind and row.get("is_current")]
        if imported:
            row = await db.scalar(
                select(CustomVoice)
                .where(CustomVoice.user_id == user_id, CustomVoice.id.in_(imported))
                .order_by(CustomVoice.version.desc())
                .limit(1),
            )
            if row:
                row.is_current = True


def _prepare_video(raw: dict[str, Any], payload: dict[str, Any], context: BackupContext) -> None:
    payload["provider_task_id"] = None


def _clear_video_handle(checkpoint: dict | None) -> dict | None:
    if checkpoint is None:
        return None
    chain = MediaChainState.model_validate(checkpoint)
    chain.queue_request_id = None
    chain.video_queue = None
    return chain.model_dump(mode="json")


async def _restore_video_jobs(db: AsyncSession, rows: dict[str, list[dict[str, Any]]], context: BackupContext) -> None:
    patches = []
    for raw in rows.get("video_gen_jobs", []):
        original = str(raw["id"])
        mapped_job = context.id_map.get("video_gen_jobs", {}).get(original)
        if mapped_job is None:
            continue
        inserted = ("video_gen_jobs", original) in context.rewriter.inserted_rows
        row = await db.get(VideoGenJob, int(mapped_job))
        if not inserted and (context.mode != "overwrite" or row.cleanup_requested_at):
            continue
        params = _VideoJobParams.model_validate(
            context.rewriter.rewrite(raw["params_json"]) if inserted else row.params_json
        )
        if params.identity_snapshot is not None:
            mapped = context.id_map.get("avatar_assets", {}).get(str(params.identity_snapshot.avatar_id))
            params = params.model_copy(
                update={
                    "identity_snapshot": params.identity_snapshot.model_copy(
                        update={"avatar_id": int(mapped) if mapped is not None else None}
                    )
                }
            )
        if params.request_scope and params.request_scope.startswith("user:") and raw.get("source_message_id"):
            mapped_source = context.id_map.get("messages", {}).get(str(raw["source_message_id"]))
            if mapped_source is not None:
                params.request_scope = f"user:{mapped_source}"
        patches.append((context.id_map["video_gen_jobs"][original], params.model_dump(mode="json")))
        if not inserted:
            row.conversation_id = context.id_map.get("conversations", {}).get(str(raw.get("conversation_id")))
            row.source_message_id = context.id_map.get("messages", {}).get(str(raw.get("source_message_id")))
            row.reply_message_id = context.id_map.get("messages", {}).get(str(raw.get("reply_message_id")))
    await db.flush()
    for start in range(0, len(patches), 500):
        payload = (
            values(column("row_id", BigInteger), column("params", JSONB))
            .data(patches[start : start + 500])
            .alias("restored_video_params")
        )
        await db.execute(
            update(VideoGenJob.__table__)
            .where(VideoGenJob.owner_id == context.user_id, VideoGenJob.id == payload.c.row_id)
            .values(params_json=payload.c.params)
        )
    ids = [
        context.id_map["video_gen_jobs"][str(raw["id"])]
        for raw in rows.get("video_gen_jobs", [])
        if ("video_gen_jobs", str(raw["id"])) in context.rewriter.inserted_rows
    ]
    await rewrite_job_checkpoints(db, context.user_id, ids, _clear_video_handle)


def _prepare_image(raw: dict[str, Any], payload: dict[str, Any], context: BackupContext) -> None:
    payload["params_json"] = ImageJobParams.model_validate(payload["params_json"]).model_dump(mode="json")


async def _restore_image_jobs(db: AsyncSession, rows: dict[str, list[dict[str, Any]]], context: BackupContext) -> None:
    for raw in rows.get("image_gen_jobs", []):
        mapped_job = context.id_map.get("image_gen_jobs", {}).get(str(raw["id"]))
        if mapped_job is None:
            continue
        inserted = ("image_gen_jobs", str(raw["id"])) in context.rewriter.inserted_rows
        row = await db.get(ImageGenJob, int(mapped_job))
        if not inserted and (context.mode != "overwrite" or row.cleanup_requested_at):
            continue
        params = ImageJobParams.model_validate(
            context.rewriter.rewrite(raw["params_json"]) if inserted else row.params_json
        )
        if params.identity_json:
            identity = VisualIdentitySnapshot.model_validate_json(params.identity_json)
            mapped = context.id_map.get("avatar_assets", {}).get(str(identity.avatar_id))
            params.identity_json = identity.model_copy(
                update={"avatar_id": int(mapped) if mapped is not None else None}
            ).model_dump_json()
        if params.request_scope.startswith("user:") and raw.get("source_message_id"):
            mapped_source = context.id_map.get("messages", {}).get(str(raw["source_message_id"]))
            if mapped_source is not None:
                params.request_scope = f"user:{mapped_source}"
        bindings = {
            media_id: str(context.id_map["messages"][str(message_id)])
            for media_id, message_id in raw["bindings_json"].items()
            if str(message_id) in context.id_map.get("messages", {})
            and (inserted or row.bindings_json.get(media_id) == str(message_id))
        }
        if not inserted:
            row.conversation_id = context.id_map.get("conversations", {}).get(str(raw.get("conversation_id")))
            row.source_message_id = context.id_map.get("messages", {}).get(str(raw.get("source_message_id")))
            await db.flush()
        row.params_json, row.bindings_json = params.model_dump(mode="json"), bindings
    await db.flush()


def _prepare_continuation(raw: dict[str, Any], payload: dict[str, Any], context: BackupContext) -> None:
    payload["automatic"] = False
    if payload["state"] != "complete":
        payload["state"] = "waiting"


async def _restore_continuation_results(
    db: AsyncSession, rows: dict[str, list[dict[str, Any]]], context: BackupContext
) -> None:
    if context.mode == "overwrite":
        ids = [int(value) for value in context.id_map.get("media_continuations", {}).values()]
        await db.execute(
            update(MediaContinuation)
            .where(MediaContinuation.user_id == context.user_id, MediaContinuation.id.in_(ids))
            .values(automatic=False)
        )
    for raw in rows.get("media_continuations", []):
        if ("media_continuations", str(raw["id"])) not in context.rewriter.inserted_rows:
            continue
        result = context.rewriter.rewrite(dict(raw.get("result_json") or {}))
        for field, table in {
            "job_id": "jobs",
            "prepared_job_id": "jobs",
            "outfit_id": "companion_outfits",
            "post_id": "companion_entries",
            "message_id": "messages",
        }.items():
            if field in result:
                mapped_result = context.id_map.get(table, {}).get(str(result[field]))
                result[field] = str(mapped_result) if mapped_result is not None else None
        await db.execute(
            update(MediaContinuation)
            .where(
                MediaContinuation.user_id == context.user_id,
                MediaContinuation.id == int(context.id_map["media_continuations"][str(raw["id"])]),
            )
            .values(result_json=result or None)
        )


def _prepare_review_attempt(raw: dict[str, Any], payload: dict[str, Any], context: BackupContext) -> None:
    if payload["state"] == "running":
        payload["state"] = "interrupted"


def _prepare_manual_operation(raw: dict[str, Any], payload: dict[str, Any], context: BackupContext) -> None:
    result = dict(payload["result_json"])
    mapped = context.id_map.get("jobs", {}).get(str(result.get("job_id")))
    result["job_id"] = int(mapped) if mapped is not None else 0
    result["start_pending"] = False
    payload["result_json"] = result


async def _restore_manual_operations(
    db: AsyncSession, rows: dict[str, list[dict[str, Any]]], context: BackupContext
) -> None:
    if context.mode != "overwrite":
        return
    ids = [int(value) for value in context.id_map.get("media_manual_operations", {}).values()]
    for operation in await db.scalars(
        select(MediaManualOperation).where(
            MediaManualOperation.user_id == context.user_id, MediaManualOperation.id.in_(ids)
        )
    ):
        if operation.result_json.get("start_pending"):
            operation.result_json = {**operation.result_json, "start_pending": False}


def backup_tables() -> tuple[BackupTable, ...]:
    return (
        BackupTable(
            "media_manual_operations",
            MediaManualOperation,
            foreign_keys={"job_id": "jobs"},
            unique_keys=("request_id",),
            prepare_payload=_prepare_manual_operation,
            restore_context=_restore_manual_operations,
        ),
        BackupTable(
            "image_gen_jobs",
            ImageGenJob,
            foreign_keys={"id": "jobs", "conversation_id": "conversations", "source_message_id": "messages"},
            prepare_payload=_prepare_image,
            restore_context=_restore_image_jobs,
        ),
        BackupTable(
            "media_review_attempts",
            MediaReviewAttempt,
            foreign_keys={"job_id": "jobs"},
            unique_keys=("request_id",),
            prepare_payload=_prepare_review_attempt,
        ),
        BackupTable(
            "media_continuations",
            MediaContinuation,
            foreign_keys={"dependency_job_id": "jobs", "owner_job_id": "jobs"},
            unique_keys=("operation_id",),
            prepare_payload=_prepare_continuation,
            restore_context=_restore_continuation_results,
        ),
        BackupTable("video_gen_jobs", VideoGenJob, prepare_payload=_prepare_video, restore_context=_restore_video_jobs),
        BackupTable(
            "custom_voices",
            CustomVoice,
            foreign_keys={"reference_asset_id": "assets", "preview_asset_id": "assets"},
            unique_keys=("token",),
            merge_unique_on_overwrite=True,
            custom_delete=_retain_versions,
            prepare_payload=_prepare,
            find_existing=_existing_version,
        ),
    )
