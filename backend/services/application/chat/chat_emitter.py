from typing import Protocol

from modules.conversation import CompanionReply


class Emitter(Protocol):
    async def send_json(self, data: dict) -> None: ...


class HeadlessEmitter:
    """无头发射器：捕获全部 ``send_json`` 帧，供子 Agent 委派、定时任务与主动陪伴回合从中取得最终结果。"""

    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_json(self, data: dict) -> None:
        self.messages.append(data)

    @property
    def final_text(self) -> str:
        for message in reversed(self.messages):
            if message.get("type") == "message.complete":
                if "reply" in message:
                    return message["content"]
                return str(message.get("text") or "")
        return ""

    @property
    def final_reply(self) -> CompanionReply | None:
        for message in reversed(self.messages):
            if message.get("type") == "message.complete":
                data = message.get("reply")
                return CompanionReply.model_validate(data) if data else None
        return None

    @property
    def final_reply_content(self) -> str | None:
        for message in reversed(self.messages):
            if message.get("type") == "message.complete":
                content = message.get("content")
                return content if isinstance(content, str) else None
        return None

    @property
    def error(self) -> str | None:
        """最近一帧 error 的诊断串：``detail`` 优先，其次是面向用户的 ``message``。"""
        for message in reversed(self.messages):
            if message.get("type") == "error":
                return str(message.get("detail") or message.get("message") or "LLM turn failed")
        return None
