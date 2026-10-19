"""窗口动作对伙伴表达提供的冻结目录；动作与素材资格由本模块判断。"""

import json
from dataclasses import dataclass
from typing import Literal

from infrastructure.assets.asset_store import resolve_asset_reference
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.accounts.models import lock_user_row
from features.appearance.queries import active_outfit_id

from .materials import AcceptedVideoActionAsset, accepted_action_asset
from .models import CompanionActionPack
from .policy import is_expression_action
from .repository import list_pack_actions


@dataclass(frozen=True)
class ExpressionActionSnapshot:
    action_id: int
    definition_id: int | None
    name: str
    motion_description: str
    use_when: tuple[str, ...]
    avoid_when: tuple[str, ...]
    kind: Literal["once", "loop"]
    duration_seconds: float


@dataclass(frozen=True)
class WindowExpressionCatalog:
    target_id: int
    epoch: int
    state_version: int
    outfit_id: int | None
    source_visual_key: str
    items: tuple[ExpressionActionSnapshot, ...]


async def window_expression_catalog(
    db: AsyncSession,
    user_id: int,
    *,
    lock: bool = False,
) -> WindowExpressionCatalog | None:
    if lock:
        await lock_user_row(db, user_id)
    statement = (
        select(CompanionActionPack)
        .where(
            CompanionActionPack.user_id == user_id,
            CompanionActionPack.active.is_(True),
        )
        .execution_options(populate_existing=True)
    )
    pack = await db.scalar(statement)
    if pack is None or pack.outfit_id != await active_outfit_id(db, user_id):
        return None
    items: list[ExpressionActionSnapshot] = []
    for row in await list_pack_actions(db, pack.id, enabled_only=True, refresh=True):
        if not is_expression_action(row):
            continue
        material = accepted_action_asset(row)
        if not isinstance(material, AcceptedVideoActionAsset) or resolve_asset_reference(material.media_path) is None:
            continue
        items.append(
            ExpressionActionSnapshot(
                action_id=row.id,
                definition_id=row.definition_id,
                name=row.name,
                motion_description=row.motion_description,
                use_when=tuple(json.loads(row.use_when)),
                avoid_when=tuple(json.loads(row.avoid_when)),
                kind=material.kind,
                duration_seconds=material.actual_duration_ms / 1000,
            ),
        )
    return WindowExpressionCatalog(
        target_id=pack.id,
        epoch=pack.appearance_epoch,
        state_version=pack.catalog_version,
        outfit_id=pack.outfit_id,
        source_visual_key=pack.source_visual_key,
        items=tuple(sorted(items, key=lambda item: item.action_id)),
    )
