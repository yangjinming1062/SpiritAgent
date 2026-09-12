import json
from typing import Any

from components import get_logger, session_scope, tool_error
from modules.conversation import Conversation
from modules.system import ChatMessageRequest, ChatRequest

from services.contracts.delegation import DelegateAction
from services.domains.conversation import conversation_memory_scope

from .chat_emitter import HeadlessEmitter

logger = get_logger(__name__)


async def run_delegated_turn(action: DelegateAction, user_id: int, llm_config: dict, *, run_turn) -> str:
    """执行子 Agent 回合并把结果转换为父回合的 ToolResult 内容。

    子回合继承父回合的 llm_config、用户作用域与会话类型（system_preset_id / is_automation）；
    回合入口由调用方（orchestrator）注入，避免模块级循环导入。输出经 HeadlessEmitter 捕获，
    以结构化的 final_text/error 取代对输出 chunk 的拼接解析。
    """
    try:
        # 仅用于插入 Conversation 行以获取 id；显式 commit 以便 run_chat_turn 能读到新会话。
        async with session_scope() as db:
            parent_id = int(action.parent_session_id) if action.parent_session_id else None
            parent = await db.get(Conversation, parent_id) if parent_id else None
            if parent is None:
                return tool_error("Parent conversation not found")
            conversation_memory_scope(parent, user_id)
            conv = Conversation(
                user_id=user_id,
                parent_id=parent_id,
                title="Subagent Task",
                # 继承父会话类型：工作 / 自动化对话的子智能体拿对应预设与工具绑定，而不是伙伴人格。
                system_preset_id=parent.system_preset_id,
                is_automation=parent.is_automation,
            )
            db.add(conv)
            await db.commit()
            await db.refresh(conv)
            sid = str(conv.id)

        headless = HeadlessEmitter()
        req = ChatRequest(
            session_id=sid,
            message=ChatMessageRequest(
                role="user",
                content=(
                    f"You are a subagent delegated with the following task:\n\n"
                    f"{action.task_description}\n\nWork autonomously to complete it, "
                    "and your final response will be sent back to the parent agent."
                ),
            ),
        )
        await run_turn(req, llm_config, user_id, headless, session_client_context=None, track_task=None)

        if headless.error:
            return tool_error(headless.error)
        final_answer = headless.final_text or "Subagent completed without producing text output."

        result_dict: dict[str, Any] = {"success": True, "result": final_answer, "subagent_session_id": sid}
        return json.dumps(result_dict, ensure_ascii=False)
    except Exception as e:
        logger.exception("Agent delegation failed")
        return tool_error(str(e))
