from common import get_router
from fastapi import WebSocket
from services.adapters.desktop import handle_chat_websocket

router = get_router()


@router.websocket("/ws")
async def chat_websocket(websocket: WebSocket, ticket: str | None = None) -> None:
    # 握手凭据是 /api/user/ws-ticket 签发的短期 purpose=ws JWT。
    if not ticket:
        await websocket.close(code=1008)
        return
    await handle_chat_websocket(websocket, ticket)
