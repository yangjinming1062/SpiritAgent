from prompts.tools import AGENT_DELEGATE_DESC, AGENT_DELEGATE_PARAM_DESCS

from services.contracts import DelegateAction
from services.infrastructure.tool_runtime import ToolsRegistry

AGENT_DELEGATE_SCHEMA = {
    "name": "agent_delegate_tool",
    "description": AGENT_DELEGATE_DESC,
    "parameters": {
        "type": "object",
        "properties": {
            "task_description": {
                "type": "string",
                "description": AGENT_DELEGATE_PARAM_DESCS["task_description"],
            },
        },
        "required": ["task_description"],
    },
}


def agent_delegate_tool(task_description: str, parent_session_id: str | None = None, **_) -> DelegateAction:
    """只校验参数并返回控制动作；子回合的创建与执行由对话执行层的 delegation 接管。"""
    return DelegateAction(task_description=task_description, parent_session_id=parent_session_id)


def register_delegate_tool(registry: ToolsRegistry) -> None:
    registry.register("agent_delegate_tool", AGENT_DELEGATE_SCHEMA, agent_delegate_tool)
