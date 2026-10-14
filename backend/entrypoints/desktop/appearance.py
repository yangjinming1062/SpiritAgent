import asyncio
import secrets

from features.appearance.avatar_service import AvatarGenerationError, get_avatar_job_lock
from features.appearance.schemas import AvatarGenerateRequest
from features.appearance.use_cases import (
    AvatarOnboardingRequiredError,
    check_avatar_regeneration,
    regenerate_avatar_view,
)
from infrastructure.constants import JSONRPC_INVALID_PARAMS
from infrastructure.logger import get_logger
from pydantic import ValidationError
from runtime.desktop.connection import UserEventPublisher
from runtime.desktop.jsonrpc import JsonRpcError
from runtime.gateway_contracts import GatewaySession, RegisterMethod
from runtime.gateway_contracts import optional_str as _optional_str

logger = get_logger(__name__)


def register_handlers(session: GatewaySession, register: RegisterMethod) -> None:
    user_id = session.user_id
    publisher = UserEventPublisher(user_id)

    async def avatar_regenerate(params: dict) -> dict:
        # 10-60s 同步生图以后台 task 跑，立即返回 queued: true 不阻塞 WS 接收循环；结果通过 avatar.regenerated 事件回。
        feedback = _optional_str(params, "feedback")
        try:
            feedback = AvatarGenerateRequest(feedback=feedback).feedback
        except ValidationError as exc:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "头像修改描述不能超过 500 字") from exc
        mode = params.get("mode")
        if mode not in ("edit", "regenerate"):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "mode must be 'edit' or 'regenerate'")
        try:
            await check_avatar_regeneration(user_id)
        except AvatarOnboardingRequiredError as exc:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, str(exc)) from exc
        job_id = f"avatar_regen_{user_id}_{secrets.token_urlsafe(6)}"
        lock = get_avatar_job_lock(user_id)
        if lock.locked():
            return {"queued": False, "reason": "already_running", "error": "头像正在生成，请等待当前任务完成"}

        async def _run() -> None:
            try:
                asset = await regenerate_avatar_view(user_id, feedback, mode)
                payload = {"job_id": job_id, "asset_url": asset.asset_url, "id": asset.id}
            except AvatarGenerationError as exc:
                logger.warning("avatar regenerate failed", extra={"user_id": user_id, "error": exc.internal})
                payload = {"job_id": job_id, "error": str(exc)}
            except Exception:
                logger.exception("avatar regenerate unexpected failure", extra={"user_id": user_id})
                payload = {"job_id": job_id, "error": "伙伴形象生成失败，请稍后重试"}
            try:
                await publisher.push_event("avatar.regenerated", payload, session_id=None)
            except Exception:
                logger.debug("avatar.regenerated event push failed", extra={"user_id": user_id}, exc_info=True)

        session.track(asyncio.create_task(_run()))
        return {"queued": True, "job_id": job_id}

    register("avatar.regenerate", avatar_regenerate)
