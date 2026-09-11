import json
from typing import Any

from components import get_logger, session_scope, tool_error
from modules.conversation import Conversation
from modules.system import ChatMessageRequest, ChatRequest

from ..tools import REGISTRY
from .chat_emitter import HeadlessEmitter
from .orchestrator import run_chat_turn

logger = get_logger(__name__)


async def agent_delegate_tool(
    task_description: str,
    user_id: int,
    llm_config: dict,
    parent_session_id: str | None = None,
    **_,
) -> str:
    sid: str | None = None

    try:
        # 仅用于插入 Conversation 行以获取 id；显式 commit 以便 run_chat_turn 能读到新会话。
        async with session_scope() as db:
            conv = Conversation(
                user_id=user_id,
                parent_id=int(parent_session_id) if parent_session_id else None,
                title="Subagent Task",
            )
            db.add(conv)
            await db.commit()
            await db.refresh(conv)
            sid = str(conv.id)

        # HeadlessEmitter 捕获全部帧，最终结果从 chunk/message.complete/error 事件中抽取。
        headless = HeadlessEmitter()

        req = ChatRequest(
            session_id=sid,
            message=ChatMessageRequest(
                role="user",
                content=(
                    f"You are a subagent delegated with the following task:\n\n"
                    f"{task_description}\n\nWork autonomously to complete it, "
                    "and your final response will be sent back to the parent agent."
                ),
            ),
        )
        await run_chat_turn(req, llm_config, user_id, headless, session_client_context=None, track_task=None)

        chunks: list[str] = []
        for msg in headless.messages:
            if msg.get("type") == "chunk":
                chunks.append(msg.get("content", ""))
            elif msg.get("type") == "error":
                err = msg.get("message") or "subagent failed"
                return tool_error(err)

        final_answer = "".join(chunks) or "Subagent completed without producing text output."

        result_dict: dict[str, Any] = {"success": True, "result": final_answer, "subagent_session_id": sid}

        return json.dumps(result_dict, ensure_ascii=False)
    except Exception as e:
        logger.exception("Agent delegation failed")
        return tool_error(str(e))


AGENT_DELEGATE_SCHEMA = {
    "name": "agent_delegate_tool",
    "description": "Delegate a complex task to an autonomous subagent. The subagent will run independently with its own thought loop and return its final summarized answer. Use this for complex multi-step reasoning or large tasks.",
    "parameters": {
        "type": "object",
        "properties": {
            "task_description": {
                "type": "string",
                "description": "Detailed description of the task, the goal, and any context the subagent needs to know.",
            },
        },
        "required": ["task_description"],
    },
}

REGISTRY.register("agent_delegate_tool", AGENT_DELEGATE_SCHEMA, agent_delegate_tool)
