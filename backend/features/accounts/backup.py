"""账户备份仅导出产品偏好，凭据和登录记录不属于导出内容。"""

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from infrastructure.backup import BackupContext, BackupTable
from sqlalchemy.ext.asyncio import AsyncSession

from .models import User
from .security import generate_activation_token, hash_activation_token


@dataclass(frozen=True)
class BackupUserSnapshot:
    id: int
    username: str


async def create_validation_account(db: AsyncSession) -> int:
    user = User(
        username=f"backup-validation-{uuid4().hex}",
        activation_code="",
        activation_token_hash=hash_activation_token(generate_activation_token()),
        is_active=False,
    )
    db.add(user)
    await db.flush()
    return user.id


async def _restore_preferences(
    db: AsyncSession,
    payloads: list[dict[str, Any]],
    context: BackupContext,
) -> tuple[dict[str, int | str], int]:
    if context.mode != "overwrite":
        return {}, 0
    user = await db.get(User, context.user_id)
    for payload in payloads:
        user.nightly_activity_enabled = payload["nightly_activity_enabled"]
    return {}, len(payloads)


def backup_tables() -> tuple[BackupTable, ...]:
    return (
        BackupTable(
            "user_preferences",
            User,
            included_columns=("nightly_activity_enabled",),
            scope_column="id",
            custom_restore=_restore_preferences,
        ),
    )
