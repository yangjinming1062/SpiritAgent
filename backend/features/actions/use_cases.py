"""actions 请求用例；拥有事务、响应装配与后台任务受理。"""

from infrastructure.assets.asset_store import signed_companion_asset_url
from infrastructure.logger import get_logger
from infrastructure.request_errors import RequestError
from sqlalchemy.ext.asyncio import AsyncSession

from features.actions.asset_retirement import action_asset_paths, retire_action_assets
from features.actions.design import accept_proposal
from features.actions.models import REQUIRED_SYSTEM_SLOTS, CompanionAction
from features.actions.pipeline import schedule_accepted_proposal
from features.actions.policy import get_daily_budget_status
from features.actions.publishing import emit_catalog_changed, publish_action_catalog
from features.actions.repository import get_action, get_active_pack, get_playback
from features.actions.schemas import (
    ActionBudgetStatus,
    ActionCatalogResponse,
    ActionDesignRequest,
    ActionDesignResult,
    ActionPlaybackReceipt,
)
from features.actions.usage import record_play_result
from features.companion.schemas import CompanionOperationResponse
from features.media.review_service import reject_pending_action_reviews

logger = get_logger(__name__)


async def _republish_catalog(db: AsyncSession, user_id: int) -> None:
    """停用/删除后重发目录快照，客户端不再保留旧播放资格。"""
    pack = await get_active_pack(db, user_id)
    if pack is None:
        return
    try:
        await publish_action_catalog(db, pack)
    except Exception:
        # 管理操作已提交；目录失败记录诊断，等待下一次发布补齐。
        logger.warning("action catalog republish failed", extra={"pack_id": pack.id}, exc_info=True)
        await db.commit()
        return
    emit_catalog_changed(db, pack)
    await db.commit()


async def get_catalog(user_id: int, db: AsyncSession) -> ActionCatalogResponse:
    """当前包的目录指针；动作明细由客户端从 manifest_url 拉取。"""
    pack = await get_active_pack(db, user_id)
    if pack is None:
        return ActionCatalogResponse(pack_id=0, catalog_version=0, appearance_epoch=0)
    return ActionCatalogResponse(
        pack_id=pack.id,
        catalog_version=pack.catalog_version,
        appearance_epoch=pack.appearance_epoch,
        manifest_url=signed_companion_asset_url(pack.manifest_path) if pack.manifest_path else None,
    )


async def design_action(body: ActionDesignRequest, user_id: int, db: AsyncSession) -> ActionDesignResult:
    await db.commit()
    acceptance = await accept_proposal(db, user_id, body, source="user_requested")
    await db.commit()
    schedule_accepted_proposal(acceptance, user_id)
    return acceptance.result


async def submit_playback_receipt(
    play_id: str,
    receipt: ActionPlaybackReceipt,
    user_id: int,
    db: AsyncSession,
) -> CompanionOperationResponse:
    """播放回执：校验归属与状态；按 play_id 幂等聚合。"""
    entry = await get_playback(db, play_id)
    if entry is None or entry.user_id != user_id:
        raise RequestError(status_code=404, detail="播放记录不存在")
    await record_play_result(
        db,
        entry,
        status=receipt.status,
        visible_duration_ms=receipt.visible_duration_ms,
        error=receipt.error,
    )
    await db.commit()
    return CompanionOperationResponse(ok=True)


async def get_budget_status(user_id: int, db: AsyncSession) -> ActionBudgetStatus:
    return await get_daily_budget_status(db, user_id)


async def _owned_action(db: AsyncSession, user_id: int, action_id: int) -> CompanionAction:
    action = await get_action(db, action_id)
    if action is None:
        raise RequestError(status_code=404, detail="动作不存在")
    pack = await get_active_pack(db, user_id)
    if pack is None or action.pack_id != pack.id:
        raise RequestError(status_code=403, detail="动作不属于当前形象")
    return action


async def set_action_enabled(action_id: int, enabled: bool, user_id: int, db: AsyncSession) -> dict[str, bool]:
    """停用立即从自主选择与播放目录排除；系统槽位不可停用（产品必需能力）。变更后同步发布目录，客户端不再保留已停用动作的播放资格。"""
    action = await _owned_action(db, user_id, action_id)
    if action.system_slot and (not enabled):
        raise RequestError(status_code=400, detail="系统动作不可停用")
    action.enabled = enabled
    await db.commit()
    await _republish_catalog(db, user_id)
    return {"ok": True, "enabled": enabled}


async def delete_action(action_id: int, user_id: int, db: AsyncSession) -> CompanionOperationResponse:
    """删除动作与素材版本行；必需系统槽位一律不可删除。素材文件在 manifest 与版本引用释放后按引用回收，不在本请求内同步删文件；删除后同步发布目录。"""
    action = await _owned_action(db, user_id, action_id)
    if action.system_slot in REQUIRED_SYSTEM_SLOTS:
        raise RequestError(status_code=400, detail=f"必需系统动作（{action.system_slot}）不可删除")
    if action.status in ("queued", "processing"):
        raise RequestError(status_code=409, detail="动作正在制作，请等待完成后再删除")
    await reject_pending_action_reviews(db, user_id=action.user_id, action_id=action.id, media_type=action.media_type)
    await retire_action_assets(db, user_id, action_asset_paths(action))
    await db.delete(action)
    await db.commit()
    await _republish_catalog(db, user_id)
    return CompanionOperationResponse(ok=True)
