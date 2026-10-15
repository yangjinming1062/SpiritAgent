"""动作库 REST 入口：目录、提案、播放回执、额度状态与管理操作。"""

from fastapi import HTTPException
from features.actions import use_cases as _actions
from features.actions.schemas import (
    ActionBudgetStatus,
    ActionCatalogResponse,
    ActionDesignRequest,
    ActionDesignResult,
    ActionPlaybackReceipt,
)
from features.companion.schemas import CompanionOperationResponse
from infrastructure.database import DbSession
from infrastructure.request_errors import RequestError

from entrypoints.http.auth import CurrentUser
from entrypoints.http.routing import get_router

router = get_router(prefix="/api/companion/actions", tag="companion-actions")


@router.get("/catalog", response_model=ActionCatalogResponse)
async def get_catalog(user: CurrentUser, db: DbSession) -> ActionCatalogResponse:
    """当前包的目录指针；动作明细由客户端从 manifest_url 拉取。"""
    try:
        return await _actions.get_catalog(user_id=user.id, db=db)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/design", response_model=ActionDesignResult)
async def design_action(body: ActionDesignRequest, user: CurrentUser, db: DbSession) -> ActionDesignResult:
    try:
        return await _actions.design_action(body=body, user_id=user.id, db=db)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/playback/{play_id}/receipt")
async def submit_playback_receipt(
    play_id: str,
    receipt: ActionPlaybackReceipt,
    user: CurrentUser,
    db: DbSession,
) -> CompanionOperationResponse:
    """播放回执：校验归属与状态；按 play_id 幂等聚合。"""
    try:
        return await _actions.submit_playback_receipt(play_id=play_id, receipt=receipt, user_id=user.id, db=db)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get("/budget", response_model=ActionBudgetStatus)
async def get_budget_status(user: CurrentUser, db: DbSession) -> ActionBudgetStatus:
    try:
        return await _actions.get_budget_status(user_id=user.id, db=db)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/{action_id}/enabled")
async def set_action_enabled(action_id: int, enabled: bool, user: CurrentUser, db: DbSession) -> dict[str, bool]:
    """停用时同步发布目录，从自主选择与客户端播放资格中排除；产品必需的系统槽位不可停用。"""
    try:
        return await _actions.set_action_enabled(action_id=action_id, enabled=enabled, user_id=user.id, db=db)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.delete("/{action_id}", response_model=CompanionOperationResponse)
async def delete_action(action_id: int, user: CurrentUser, db: DbSession) -> CompanionOperationResponse:
    """删除动作与素材版本行；必需系统槽位一律不可删除。素材文件在 manifest 与版本引用释放后按引用回收，不在本请求内同步删文件；删除后同步发布目录。"""
    try:
        return await _actions.delete_action(action_id=action_id, user_id=user.id, db=db)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
