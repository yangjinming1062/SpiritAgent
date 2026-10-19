"""桌面与手机共用的网关票据及连接入口。"""

from fastapi import Depends, Request, Response, WebSocket
from fastapi.security import HTTPAuthorizationCredentials
from features.accounts.gateway import (
    GatewayTicketRequest,
    GatewayTicketResponse,
    consume_gateway_ticket,
    issue_gateway_ticket,
)
from features.accounts.remote.security import remote_public_base_url
from features.accounts.security import BEARER_SCHEME
from infrastructure.database import DbSession
from infrastructure.http_contracts import operation_policy

from entrypoints.desktop.handlers import handle_chat_websocket, handle_remote_websocket
from entrypoints.http.auth import get_request_identity
from entrypoints.http.routing import get_router

router = get_router(remote=True)


@router.post(
    "/ticket",
    response_model=GatewayTicketResponse,
    openapi_extra=operation_policy(ipc=False),
)
async def gateway_ticket(
    request: Request,
    response: Response,
    db: DbSession,
    body: GatewayTicketRequest | None = None,
    credentials: HTTPAuthorizationCredentials | None = Depends(BEARER_SCHEME),
) -> GatewayTicketResponse:
    identity = await get_request_identity(
        request,
        db,
        credentials,
        content_only=False,
        stream_id=body.stream_id if body else None,
    )
    response.headers["Cache-Control"] = "no-store"
    return issue_gateway_ticket(identity)


@router.websocket("/ws")
async def gateway_websocket(websocket: WebSocket, ticket: str = "") -> None:
    identity = consume_gateway_ticket(ticket)
    if identity is None:
        await websocket.close(code=4401, reason="网关票据无效或已过期")
        return
    if identity.device_id is not None:
        if websocket.headers.get("origin") != remote_public_base_url():
            await websocket.close(code=4403, reason="远程请求来源无效")
            return
        await handle_remote_websocket(websocket, identity)
    else:
        await handle_chat_websocket(websocket, identity)
