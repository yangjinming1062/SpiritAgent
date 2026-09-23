"""动作库 REST 入口：目录、提案、播放回执、额度状态与管理操作。"""

import json
from pathlib import Path

from common import get_router
from components import SETTINGS, DbSession
from fastapi import HTTPException
from modules.auth import CurrentUser
from modules.companion import (
    REQUIRED_SYSTEM_SLOTS,
    ActionBudgetStatus,
    ActionCatalogResponse,
    ActionDesignRequest,
    ActionDesignResult,
    ActionPlaybackReceipt,
    ActionSummary,
    CompanionAction,
)
from modules.ws import emit_ws_event
from services.application.actions import accept_proposal
from services.application.actions.pipeline import schedule_action_generation, schedule_proposal_review
from services.domains.actions import (
    get_action,
    get_active_pack,
    get_daily_budget_status,
    get_playback,
    list_pack_actions,
    publish_action_catalog,
    record_play_result,
)
from services.infrastructure.assets import signed_companion_asset_url
from sqlalchemy.ext.asyncio import AsyncSession

router = get_router(prefix="/api/companion/actions", tag="companion-actions")


async def _republish_catalog(db: AsyncSession, user_id: int) -> None:
    """停用/删除后重发目录快照，客户端不再保留旧播放资格。"""
    pack = await get_active_pack(db, user_id)
    if pack is None:
        return
    assets_dir = Path(SETTINGS.data_dir) / "companion-assets" / str(user_id)
    assets_dir.mkdir(parents=True, exist_ok=True)
    try:
        version = await publish_action_catalog(db, pack, assets_dir=assets_dir)
    except Exception:  # noqa: BLE001 — 目录不足以重发时保留数据库变更，不回滚管理操作
        await db.commit()
        return
    emit_ws_event(
        db,
        user_id=user_id,
        event_type="companion.action.catalog_changed",
        payload={"packId": pack.id, "catalogVersion": version},
    )
    await db.commit()


@router.get("/catalog", response_model=ActionCatalogResponse)
async def get_catalog(user: CurrentUser, db: DbSession) -> ActionCatalogResponse:
    pack = await get_active_pack(db, user.id)
    if pack is None:
        return ActionCatalogResponse(pack_id=0, catalog_version=0, actions=[])

    actions = await list_pack_actions(db, pack.id, enabled_only=False)
    summaries = [
        ActionSummary(
            action_id=action.id,
            key=action.key,
            name=action.name,
            system_slot=action.system_slot or "",
            kind=action.kind,
            motion_description=action.motion_description,
            use_when=json.loads(action.use_when or "[]"),
            avoid_when=json.loads(action.avoid_when or "[]"),
            tags=json.loads(action.tags or "[]"),
            enabled=action.enabled,
        )
        for action in actions
    ]
    manifest_url = signed_companion_asset_url(pack.manifest_path) if pack.manifest_path else None
    return ActionCatalogResponse(
        pack_id=pack.id,
        catalog_version=pack.catalog_version,
        manifest_url=manifest_url,
        actions=summaries,
    )


@router.post("/design", response_model=ActionDesignResult)
async def design_action(body: ActionDesignRequest, user: CurrentUser, db: DbSession) -> ActionDesignResult:
    result = await accept_proposal(db, user.id, body, source="user_requested")
    await db.commit()
    retry_pack_id = None
    if result.outcome == "pending_review" and result.action_id is not None and result.proposal_id is None:
        action = await get_action(db, result.action_id)
        retry_pack_id = action.pack_id if action is not None else None
    if result.outcome == "pending_review" and result.proposal_id is not None:
        schedule_proposal_review(result.proposal_id, user.id)
    elif result.outcome == "pending_review" and result.action_id is not None and retry_pack_id is not None:
        schedule_action_generation(retry_pack_id, result.action_id, user.id)
    return result


@router.post("/playback/{play_id}/receipt")
async def submit_playback_receipt(
    play_id: str,
    receipt: ActionPlaybackReceipt,
    user: CurrentUser,
    db: DbSession,
) -> dict:
    """播放回执：校验归属与状态；按 play_id 幂等聚合。"""
    entry = await get_playback(db, play_id)
    if entry is None or entry.user_id != user.id:
        raise HTTPException(status_code=404, detail="播放记录不存在")
    await record_play_result(
        db,
        play_id,
        status=receipt.status,
        visible_duration_ms=receipt.visible_duration_ms,
        error=receipt.error,
    )
    await db.commit()
    return {"ok": True}


@router.get("/budget", response_model=ActionBudgetStatus)
async def get_budget_status(user: CurrentUser, db: DbSession) -> ActionBudgetStatus:
    return await get_daily_budget_status(db, user.id)


async def _owned_action(db: AsyncSession, user_id: int, action_id: int) -> CompanionAction:
    action = await get_action(db, action_id)
    if action is None:
        raise HTTPException(status_code=404, detail="动作不存在")
    pack = await get_active_pack(db, user_id)
    if pack is None or action.pack_id != pack.id:
        raise HTTPException(status_code=403, detail="动作不属于当前形象")
    return action


@router.post("/{action_id}/enabled")
async def set_action_enabled(
    action_id: int,
    enabled: bool,
    user: CurrentUser,
    db: DbSession,
) -> dict:
    """停用立即从自主选择与播放目录排除；系统槽位不可停用（产品必需能力）。
    变更后同步发布目录，客户端不再保留已停用动作的播放资格。"""
    action = await _owned_action(db, user.id, action_id)
    if action.system_slot and not enabled:
        raise HTTPException(status_code=400, detail="系统动作不可停用")
    action.enabled = enabled
    await db.commit()
    await _republish_catalog(db, user.id)
    return {"ok": True, "enabled": enabled}


@router.delete("/{action_id}")
async def delete_action(action_id: int, user: CurrentUser, db: DbSession) -> dict:
    """删除动作与素材版本行；必需系统槽位必须保有可用替代后才能移除。
    素材文件在 manifest 与版本引用释放后按引用回收，不在本请求内同步删文件。
    删除后同步发布目录，基础调度不再引用已移除动作。"""
    action = await _owned_action(db, user.id, action_id)
    if action.system_slot in REQUIRED_SYSTEM_SLOTS:
        raise HTTPException(status_code=400, detail=f"必需系统动作（{action.system_slot}）不可删除")
    await db.delete(action)
    await db.commit()
    await _republish_catalog(db, user.id)
    return {"ok": True}
