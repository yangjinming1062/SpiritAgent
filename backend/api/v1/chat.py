from common import get_router
from fastapi import WebSocket
from services.adapters.desktop import handle_chat_websocket

router = get_router()


@router.websocket("/ws")
async def chat_websocket(websocket: WebSocket, ticket: str | None = None, token: str | None = None) -> None:
    # renderer 用短期 ?ticket= JWT 鉴权；?token= 仅供后端内部调用。
    credential = ticket or token
    if not credential:
        await websocket.close(code=1008)
        return
    await handle_chat_websocket(websocket, credential)
