from collections import deque
from enum import StrEnum
from typing import Protocol

from features.conversation.replies import CompanionReply
from infrastructure.text import safe_json_loads
from runtime.tools.tool_result_classification import tool_may_have_effects


class Emitter(Protocol):
    async def send_json(self, data: dict) -> None: ...


class ToolReceiptState(StrEnum):
    AVAILABLE = "available"
    PENDING = "pending"
    STATUS_ONLY = "status_only"
    INCOMPLETE = "incomplete"
    UNVERIFIED = "unverified"


def _tool_receipt(name: str, content: object) -> tuple[ToolReceiptState, str]:
    is_video = name in ("video_generate", "video_generate_status")
    if isinstance(content, list):
        parts = [part for part in content if isinstance(part, dict)]
        text = "\n".join(
            part["text"] for part in parts if part.get("type") == "input_text" and isinstance(part.get("text"), str)
        )
        has_image = any(
            part.get("type") == "input_image" and isinstance(part.get("image_url"), str) and part["image_url"]
            for part in parts
        )
        if has_image and not is_video:
            return ToolReceiptState.AVAILABLE, f"[工具返回图像供本轮使用，未验证向用户交付] {text}".rstrip()
        content = text
    if not isinstance(content, str) or not content.strip():
        return ToolReceiptState.UNVERIFIED, ""
    payload = safe_json_loads(content)
    if not isinstance(payload, dict):
        state = ToolReceiptState.UNVERIFIED if is_video else ToolReceiptState.AVAILABLE
        return state, content
    if payload.get("ok") is False:
        return ToolReceiptState.UNVERIFIED, content
    status = payload.get("status")
    if payload.get("success") is False:
        state = ToolReceiptState.UNVERIFIED if status == "result_unknown" else ToolReceiptState.INCOMPLETE
        return state, content
    if is_video:
        task_id, url = payload.get("task_id"), payload.get("url")
        if not isinstance(task_id, str) or not task_id.strip():
            return ToolReceiptState.UNVERIFIED, content
        has_video = isinstance(url, str) and bool(url.strip())
        if name == "video_generate_status" and isinstance(status, str) and status:
            state = ToolReceiptState.AVAILABLE if status == "succeeded" and has_video else ToolReceiptState.STATUS_ONLY
            return state, content
        if name == "video_generate" and payload.get("success") is True:
            if payload.get("pending") is True:
                return ToolReceiptState.PENDING, content
            if has_video:
                return ToolReceiptState.AVAILABLE, content
        return ToolReceiptState.UNVERIFIED, content
    if name in ("terminal", "execute_code"):
        exit_code = payload.get("exit_code")
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
    """保存委派、定时与主动回合结算需要的最后答复、错误和工具证据。"""

    def __init__(self) -> None:
        self._final: dict = {}
        self.error_frame: dict | None = None
        self.tools_may_have_effects = False
        self.tool_evidence: deque[dict[str, str]] = deque(maxlen=4)
        self.tool_result_count = 0

    async def send_json(self, data: dict) -> None:
        match data.get("type"):
            case "message.complete":
                self._final = data
            case "error":
                self.error_frame = data
            case "tool_start":
                self.tools_may_have_effects |= tool_may_have_effects(data.get("name"))

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
        if "reply" in self._final:
            return self._final["content"]
        return str(self._final.get("text") or "")

    @property
    def final_reply(self) -> CompanionReply | None:
        reply = self._final.get("reply")
        return CompanionReply.model_validate(reply) if reply else None

    @property
    def final_reply_content(self) -> str | None:
        content = self._final.get("content")
        return content if isinstance(content, str) else None

    @property
    def error(self) -> str | None:
        if self.error_frame is None:
            return None
        return str(self.error_frame.get("detail") or self.error_frame.get("message") or "LLM turn failed")
