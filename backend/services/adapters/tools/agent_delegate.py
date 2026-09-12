from services.contracts.delegation import DelegateAction
from services.infrastructure.tool_runtime import ToolsRegistry

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


def agent_delegate_tool(task_description: str, parent_session_id: str | None = None, **_) -> DelegateAction:
    """只校验参数并返回控制动作；子回合的创建与执行由对话执行层的 delegation 接管。"""
    return DelegateAction(task_description=task_description, parent_session_id=parent_session_id)


def register_delegate_tool(registry: ToolsRegistry) -> None:
    registry.register("agent_delegate_tool", AGENT_DELEGATE_SCHEMA, agent_delegate_tool)
