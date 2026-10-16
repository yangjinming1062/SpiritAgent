"""动作库 REST 入口：目录、提案、播放回执、额度状态与管理操作。"""

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
from infrastructure.http_contracts import operation_policy
from pydantic import BaseModel, ConfigDict

from entrypoints.http.auth import CurrentUser
from entrypoints.http.routing import get_router

router = get_router(prefix="/api/companion/actions", tag="companion-actions")


@router.get("/catalog", response_model=ActionCatalogResponse)
async def get_catalog(user: CurrentUser, db: DbSession) -> ActionCatalogResponse:
    """当前包的目录指针；动作明细由客户端从 manifest_url 拉取。"""
    return await _actions.get_catalog(user_id=user.id, db=db)


@router.post(
    "/design",
    response_model=ActionDesignResult,
    openapi_extra=operation_policy(wait_for_model=True),
)
async def design_action(body: ActionDesignRequest, user: CurrentUser, db: DbSession) -> ActionDesignResult:
    return await _actions.design_action(body=body, user_id=user.id, db=db)


@router.post("/playback/{play_id}/receipt")
async def submit_playback_receipt(
    play_id: str,
    receipt: ActionPlaybackReceipt,
    user: CurrentUser,
    db: DbSession,
) -> CompanionOperationResponse:
    """播放回执：校验归属与状态；按 play_id 幂等聚合。"""
    return await _actions.submit_playback_receipt(play_id=play_id, receipt=receipt, user_id=user.id, db=db)


@router.get("/budget", response_model=ActionBudgetStatus)
async def get_budget_status(user: CurrentUser, db: DbSession) -> ActionBudgetStatus:
    return await _actions.get_budget_status(user_id=user.id, db=db)


class ActionUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


@router.patch("/{action_id}")
async def set_action_enabled(
    action_id: int,
    body: ActionUpdateRequest,
    user: CurrentUser,
    db: DbSession,
) -> dict[str, bool]:
    """停用时同步发布目录，从自主选择与客户端播放资格中排除；产品必需的系统槽位不可停用。"""
    return await _actions.set_action_enabled(action_id=action_id, enabled=body.enabled, user_id=user.id, db=db)


@router.delete(
    "/{action_id}",
    response_model=CompanionOperationResponse,
)
async def delete_action(action_id: int, user: CurrentUser, db: DbSession) -> CompanionOperationResponse:
    """删除动作与素材版本行；必需系统槽位一律不可删除。素材文件在 manifest 与版本引用释放后按引用回收，不在本请求内同步删文件；删除后同步发布目录。"""
    return await _actions.delete_action(action_id=action_id, user_id=user.id, db=db)
