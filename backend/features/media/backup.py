"""音色方案的备份与恢复贡献。"""

from typing import Any
from uuid import UUID

from infrastructure.assets.paths import parse_companion_asset_path
from infrastructure.backup import BackupContext, BackupImportMode, BackupTable
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

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
        row.reference_path = row.reference_path or payload.get("reference_path")
        row.preview_path = row.preview_path or payload.get("preview_path", "")
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


def backup_tables() -> tuple[BackupTable, ...]:
    return (
        BackupTable(
            "custom_voices",
            CustomVoice,
            unique_keys=("token",),
            merge_unique_on_overwrite=True,
            custom_delete=_retain_versions,
            prepare_payload=_prepare,
            find_existing=_existing_version,
        ),
    )
