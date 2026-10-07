import asyncio
from typing import Any, Protocol

from components import SESSION_LOCAL

from services.domains.conversation import build_session_messages
from services.infrastructure.desktop import redact_message

from .runtime import ActiveTurnSnapshot, ToolSnapshot


class EventPublisher(Protocol):
    async def push_event(self, event_type: str, payload: Any = None, session_id: str | None = None) -> None: ...


_TRANSLATED: dict[str, str] = {
    "chunk": "message.delta",
    "reasoning.delta": "message.reasoning.delta",
    "message.start": "message.start",
    "bubble.break": "message.break",
    "bubble.append": "message.bubble",
    "message.complete": "message.complete",
    "message.persisted": "message.persisted",
    "tool_start": "tool.start",
    "tool_end": "tool.complete",
    "error": "error",
    "compress.completed": "compress.completed",
}


class JsonRpcEmitter:
    """翻译回合帧并维护恢复快照；快照与事件发布共用锁，保持挂载水位一致。"""

    def __init__(
        self,
        *,
        dispatcher: EventPublisher,
        session_id: str,
        active_turn: ActiveTurnSnapshot | None = None,
        snapshot_lock: asyncio.Lock | None = None,
    ) -> None:
        self._dispatcher = dispatcher
        self._session_id = session_id
        self.active_turn = active_turn
        self.failed = False
        self.error: str | None = None
        self._snapshot_lock = snapshot_lock or asyncio.Lock()

    async def send_json(self, data: dict) -> None:
        async with self._snapshot_lock:
            await self._send_json(data)

    async def _send_json(self, data: dict) -> None:
        raw_type = data.get("type")
        if not isinstance(raw_type, str) or raw_type not in _TRANSLATED:
            return
        event_name = _TRANSLATED[raw_type]
        payload = self._translate(raw_type, data)
        turn = self.active_turn
        if turn is not None:
            if raw_type in ("message.start", "bubble.break"):
                if turn.text.strip():
                    turn.bubbles.append({"type": "text", "text": turn.text})
                turn.text = ""
            elif raw_type == "chunk":
                turn.text += payload.get("text", "")
            elif raw_type == "reasoning.delta":
                turn.reasoning += payload.get("text", "")
            elif raw_type == "bubble.append" and payload.get("bubble"):
                bubbles = turn.bubbles
                index = payload.get("bubble_index")
                if isinstance(index, int) and 0 <= index < len(bubbles):
                    bubbles[index] = payload["bubble"]
                else:
                    bubbles.append(payload["bubble"])
            elif raw_type in ("tool_start", "tool_end"):
                turn.tools[:] = [tool for tool in turn.tools if tool.call_id != payload.get("call_id")]
                turn.tools.append(ToolSnapshot(payload.get("name"), payload.get("call_id"), payload["status"]))
            elif raw_type == "message.persisted":
                turn.message_ids = payload["message_ids"]
                # 同一投影用于发送端对账及其他设备插入用户消息。
                async with SESSION_LOCAL() as db:
                    messages = await build_session_messages(int(self._session_id), db, only_ids=payload["message_ids"])
                payload["messages"] = messages
                turn.messages = payload["messages"]
            elif raw_type == "error":
                self.failed = True
                self.error = payload.get("message")
            if raw_type in ("error", "message.complete"):
                turn.running = False
            payload["request_id"] = turn.request_id
            payload["origin_kind"] = turn.origin_kind
        await self._dispatcher.push_event(event_name, payload, session_id=self._session_id)

    @staticmethod
    def _translate(raw_type: str, data: dict) -> Any:
        if raw_type in ("chunk", "reasoning.delta"):
            return {
                "text": data.get("content", ""),
            }
        if raw_type in ("tool_start", "tool_end"):
            return {
                "name": data.get("name"),
                "call_id": data.get("call_id"),
                "status": "complete" if raw_type == "tool_end" else "running",
            }
        if raw_type == "error":
            # 错误帧在此离开服务端边界：与 push_error_event 一样脱敏；``detail`` 只供无头消费者，不下发。
            message = data.get("message", "Unknown error")
            return {
                "message": redact_message(message) if isinstance(message, str) else message,
                **({"retry_message_id": data["retry_message_id"]} if type(data.get("retry_message_id")) is int else {}),
            }
        if raw_type == "bubble.append":
            bubble = data.get("bubble")
            return {
                **({"message_id": data["message_id"]} if isinstance(data.get("message_id"), int) else {}),
                **({"bubble_index": data["bubble_index"]} if isinstance(data.get("bubble_index"), int) else {}),
                **({"bubble": bubble} if isinstance(bubble, dict) else {}),
            }
        if raw_type == "message.complete":
            usage = data.get("usage")
            return {
                **({"text": data["text"]} if "text" in data else {}),
                **({"bubbles": data["bubbles"]} if isinstance(data.get("bubbles"), list) else {}),
                **({"reasoning": data["reasoning"]} if data.get("reasoning") else {}),
                **({"media": data["media"]} if isinstance(data.get("media"), list) else {}),
                **({"usage": usage} if isinstance(usage, dict) else {}),
                **({"message_id": data["message_id"]} if isinstance(data.get("message_id"), int) else {}),
            }
        if raw_type == "message.persisted":
            raw_ids = data.get("message_ids")
            message_ids = [i for i in raw_ids if isinstance(i, int)] if isinstance(raw_ids, list) else []
            return {"role": data.get("role"), "message_ids": message_ids}
        if raw_type == "compress.completed":
            # text 与持久化 Message.content 同源；message_id 给客户端挂载 backendMessageId 留口子。
            return {
                "subtype": data.get("subtype", "compress_summary"),
                "text": data.get("text", ""),
                **({"message_id": data["message_id"]} if isinstance(data.get("message_id"), int) else {}),
            }
        # 兜底：message.start / bubble.break 均为空载荷。
        return {}
