from typing import Protocol


class Emitter(Protocol):
    async def send_json(self, data: dict) -> None: ...


class HeadlessEmitter:
    """子代理发射器：捕获全部 ``send_json`` 帧，供工具把一轮事件抽取为最终结果。"""

    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_json(self, data: dict) -> None:
        self.messages.append(data)

    @property
    def final_text(self) -> str:
        for message in reversed(self.messages):
            if message.get("type") == "message.complete":
                return str(message.get("text") or "")
        return ""

    @property
    def error(self) -> str | None:
        for message in reversed(self.messages):
            if message.get("type") == "error":
                return str(message.get("message") or "LLM turn failed")
        return None
