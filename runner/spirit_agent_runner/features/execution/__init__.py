"""终端、后台进程、代码脚本和执行环境。"""

from spirit_agent_runner.contracts.tooling import ToolDefinition
from spirit_agent_runner.features.execution.code.code_execution_tool import get_tool_definitions as code_tools
from spirit_agent_runner.features.execution.process.process_tool import get_tool_definitions as process_tools
from spirit_agent_runner.features.execution.terminal.terminal_tool import get_tool_definitions as terminal_tools


def get_tool_definitions() -> list[ToolDefinition]:
    return [*terminal_tools(), *process_tools(), *code_tools()]
