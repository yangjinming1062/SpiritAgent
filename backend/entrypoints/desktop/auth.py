from dataclasses import dataclass

import jwt
from features.accounts.security import decode_access_token
from features.accounts.session_operations import is_login_active
from infrastructure.logger import get_logger
from pydantic import ValidationError
from runtime.client_context import ChatRequestClientContext

logger = get_logger(__name__)


@dataclass(frozen=True)
class WsTicket:
    user_id: int
    login_record_id: int
    client_context: ChatRequestClientContext | None


def decode_ws_ticket(token: str) -> WsTicket | None:
    """校验 WS ticket 签名、用途与身份声明；用户与登录记录的有效性由握手锁内的 ``is_ws_login_active`` 核对。"""
    if not token:
        return None
    try:
        payload = decode_access_token(token)
    except jwt.PyJWTError as exc:
        logger.info("WS token decode failed", extra={"error": str(exc)})
        return None

    if payload.get("purpose") != "ws":
        logger.info("WS token missing purpose=ws claim; renderer must use /api/user/ws-ticket")
        return None

    try:
        user_id = int(payload["sub"])
        login_record_id = int(payload["login_id"])
    except (KeyError, TypeError, ValueError):
        return None

    client_context: ChatRequestClientContext | None = None
    if (raw_ctx := payload.get("ctx")) is not None:
        try:
            client_context = ChatRequestClientContext.model_validate(raw_ctx)
        except ValidationError:
            logger.debug("client context parse failed; continuing without context", extra={"user_id": user_id})
    return WsTicket(user_id, login_record_id, client_context)


async def is_ws_login_active(user_id: int, login_record_id: int) -> bool:
    return await is_login_active(user_id, login_record_id)
