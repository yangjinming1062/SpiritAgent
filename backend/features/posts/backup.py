"""模块拥有的备份表、校验与恢复操作。"""

from functools import partial
from typing import Any

from infrastructure.assets.paths import parse_companion_asset_path
from infrastructure.backup import BackupContext, BackupTable
from sqlalchemy.ext.asyncio import AsyncSession

from .models import CompanionPost, CompanionPostComment, PostCommentRole, PostContentType
from .schemas import PostCommentResponse, PostContext


def _prepare(table: str, raw: dict[str, Any], payload: dict[str, Any], context: BackupContext) -> None:

    if table == "companion_post_comments":
        payload["reply_to_comment_id"] = None
        if payload.get("reply_status") in {"pending", "running"}:
            payload["reply_status"] = "failed"
            payload["reply_error"] = "恢复的评论可手动重试回复"


async def _check(
    table: str,
    db: AsyncSession,
    raw: dict[str, Any],
    payload: dict[str, Any],
    context: BackupContext,
) -> None:
    if table == "companion_posts":
        if not isinstance(payload.get("is_read"), bool):
            raise ValueError("Invalid post read state")
        if payload.get("content_type") not in {kind.value for kind in PostContentType}:
            raise ValueError("Invalid post content type")
        PostContext.model_validate(payload["context_json"])
        if payload["content_type"] != "text" and not payload.get("media_url"):
            raise ValueError("Post main media is missing")
        for path in (payload.get("media_url"), payload.get("audio_url")):
            if path and (not (parsed := parse_companion_asset_path(path)) or parsed[0] != context.asset_owner_id):
                raise ValueError("Post media must belong to the target account")
    if table == "companion_post_comments":
        if payload.get("role") not in {role.value for role in PostCommentRole}:
            raise ValueError("Invalid post comment role")
        PostCommentResponse.model_validate({**payload, "id": raw["id"]})
        if (payload["role"] == "companion") != (payload["reply_status"] == "none"):
            raise ValueError("Invalid reply status for comment role")


async def _reply_links(
    db: AsyncSession,
    rows: list[dict[str, Any]],
    new_map: dict[str, int | str],
    context: BackupContext,
) -> None:
    originals = {str(raw["id"]): raw for raw in rows}
    for raw in rows:
        target_id = raw.get("reply_to_comment_id")
        if target_id is None:
            continue
        target = originals.get(str(target_id))
        if (
            target is None
            or target["post_id"] != raw["post_id"]
            or target["role"] != "user"
            or raw["role"] != "companion"
        ):
            raise ValueError("Invalid post comment reply target")
        row = await db.get(CompanionPostComment, new_map[str(raw["id"])])
        row.reply_to_comment_id = new_map[str(target_id)]


def backup_tables() -> tuple[BackupTable, ...]:
    return (
        BackupTable("companion_posts", CompanionPost, validate_payload=partial(_check, "companion_posts")),
        BackupTable(
            "companion_post_comments",
            CompanionPostComment,
            foreign_keys={"post_id": "companion_posts"},
            prepare_payload=partial(_prepare, "companion_post_comments"),
            validate_payload=partial(_check, "companion_post_comments"),
            after_rows=_reply_links,
        ),
    )
