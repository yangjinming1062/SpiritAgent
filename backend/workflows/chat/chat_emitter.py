from collections import deque
from enum import StrEnum
from typing import Protocol

from features.conversation.replies import CompanionReply
from infrastructure.text import safe_json_loads


class Emitter(Protocol):
    async def send_json(self, data: dict) -> None: ...


class ToolReceiptState(StrEnum):
    AVAILABLE = "available"
    PENDING = "pending"
    INCOMPLETE = "incomplete"
    UNVERIFIED = "unverified"


def _tool_receipt(name: str, content: object) -> tuple[ToolReceiptState, str]:
    if isinstance(content, list):
        parts = [part for part in content if isinstance(part, dict)]
        text = "\n".join(
            part["text"] for part in parts if part.get("type") == "input_text" and isinstance(part.get("text"), str)
        )
        has_image = any(
            part.get("type") == "input_image" and isinstance(part.get("image_url"), str) and part["image_url"]
            for part in parts
        )
        if has_image:
            return ToolReceiptState.AVAILABLE, f"[工具返回图像供本轮使用，未验证向用户交付] {text}".rstrip()
        content = text
    if not isinstance(content, str) or not content.strip():
        return ToolReceiptState.UNVERIFIED, ""
    payload = safe_json_loads(content)
    if isinstance(payload, dict):
        if payload.get("ok") is False:
            return ToolReceiptState.UNVERIFIED, content
        if payload.get("success") is False:
            state = (
                ToolReceiptState.UNVERIFIED
                if payload.get("status") == "result_unknown"
                else ToolReceiptState.INCOMPLETE
            )
            return state, content
        if name == "video_generate" and payload.get("pending") is True and payload.get("success") is True:
            return ToolReceiptState.PENDING, content
        if name in ("terminal", "execute_code"):
            status, exit_code = payload.get("status"), payload.get("exit_code")
            if status in ("error", "blocked", "cancelled"):
                return ToolReceiptState.INCOMPLETE, content
            if status in ("timeout", "interrupted") or exit_code in (124, 130):
                return ToolReceiptState.UNVERIFIED, content
            if isinstance(exit_code, int) and exit_code != 0:
                return ToolReceiptState.INCOMPLETE, content
            if name == "terminal" and exit_code == 0 and isinstance(payload.get("session_id"), str):
                return ToolReceiptState.PENDING, content
    return ToolReceiptState.AVAILABLE, content


class HeadlessEmitter:
    """无头发射器：捕获全部 ``send_json`` 帧，供子 Agent 委派、定时任务与主动陪伴回合从中取得最终结果。"""

    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.tool_evidence: deque[dict[str, str]] = deque(maxlen=4)
        self.tool_result_count = 0

    async def send_json(self, data: dict) -> None:
        self.messages.append(data)

    def record_tool_results(self, results: list[dict]) -> None:
        for result in results:
            name = str(result.get("name", ""))
            state, content = _tool_receipt(name, result.get("receipt_content", result.get("content")))
            if len(content) > 140:
                content = f"{content[:80]}…{content[-60:]}"
            self.tool_evidence.append({"tool": name, "result_excerpt": content, "receipt_state": state.value})
            self.tool_result_count += 1

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
