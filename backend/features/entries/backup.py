"""完整新结构的内容、引用、评论和资产关联备份。"""

from typing import Any

from infrastructure.backup import BackupContext, BackupRows, BackupTable
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import CompanionEntry, EntryAsset, EntryComment, EntryLink
from .ports import rebuild_entry_projections


def _prepare_comment(_raw: dict[str, Any], payload: dict[str, Any], _context: BackupContext) -> None:
    payload["reply_to_comment_id"] = None
    if payload.get("reply_status") in {"pending", "running"}:
        payload.update(reply_status="failed", reply_error="恢复的评论可手动重试回复")


async def _link_replies(
    db: AsyncSession, rows: list[dict[str, Any]], new_map: dict[str, int | str], context: BackupContext
) -> None:
    originals = {str(row["id"]): row for row in rows}
    updates = []
    for row in rows:
        if ("entry_comments", str(row["id"])) not in context.rewriter.inserted_rows:
            continue
        target_id = row.get("reply_to_comment_id")
        if target_id is None:
            continue
        target = originals.get(str(target_id))
        if (
            target is None
            or target["post_id"] != row["post_id"]
            or target["role"] != "user"
            or row["role"] != "companion"
        ):
            raise ValueError("Invalid comment reply target")
        updates.append({"id": int(new_map[str(row["id"])]), "reply_to_comment_id": int(new_map[str(target_id)])})
    if updates:
        await db.execute(update(EntryComment), updates)


async def _restore_projections(db: AsyncSession, _rows: BackupRows, context: BackupContext) -> None:
    await rebuild_entry_projections(db, context.user_id)


def backup_tables() -> tuple[BackupTable, ...]:
    return (
        BackupTable(
            "companion_entries", CompanionEntry, unique_keys=("source_key",), restore_context=_restore_projections
        ),
        BackupTable(
            "entry_links",
            EntryLink,
            foreign_keys={"source_id": "companion_entries", "target_id": "companion_entries"},
            unique_keys=("source_id", "target_id", "kind"),
            strict_references=True,
        ),
        BackupTable(
            "entry_assets",
            EntryAsset,
            foreign_keys={"entry_id": "companion_entries", "asset_id": "assets"},
            unique_keys=("entry_id", "role", "position"),
            strict_references=True,
        ),
        BackupTable(
            "entry_comments",
            EntryComment,
            foreign_keys={"post_id": "companion_entries"},
            unique_keys=("source_key",),
            prepare_payload=_prepare_comment,
            after_rows=_link_replies,
            strict_references=True,
        ),
    )
