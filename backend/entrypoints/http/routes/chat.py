from fastapi import WebSocket
from features.accounts.security import consume_ws_ticket

from entrypoints.desktop.handlers import handle_chat_websocket
from entrypoints.http.routing import get_router

router = get_router()


@router.websocket("/ws")
async def chat_websocket(websocket: WebSocket, ticket: str | None = None) -> None:
    # 握手凭据是 /api/user/ws-ticket 签发的短期 purpose=ws JWT。
    if not ticket or not consume_ws_ticket(ticket):
        await websocket.close(code=1008)
        return
    await handle_chat_websocket(websocket, ticket)
