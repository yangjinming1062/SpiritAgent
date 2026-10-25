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
from .models import VideoGenJob
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
        if ("video_gen_jobs", original) not in context.rewriter.inserted_rows:
            continue
        params = _VideoJobParams.model_validate(context.rewriter.rewrite(raw["params_json"]))
        if params.identity_snapshot is not None:
            mapped = context.id_map.get("avatar_assets", {}).get(str(params.identity_snapshot.avatar_id))
            params = params.model_copy(
                update={
                    "identity_snapshot": params.identity_snapshot.model_copy(
                        update={"avatar_id": int(mapped) if mapped is not None else None}
                    )
                }
            )
        patches.append((context.id_map["video_gen_jobs"][original], params.model_dump(mode="json")))
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


def backup_tables() -> tuple[BackupTable, ...]:
    return (
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
