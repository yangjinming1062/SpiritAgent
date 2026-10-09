"""工具运行时基础设施：注册表、执行防护、参数矫正与工具结果处理。"""

from .domains import apply_search_tools_catalog, search_domains_and_tools
from .model_tools import coerce_tool_args
from .presentation import DESKTOP_ACTION_TOOL_NAMES, WINDOW_ACTION_TOOL_NAMES, unavailable_presentation_tool_names
from .registry import REGISTRY, RESERVED_KEYS, ToolsRegistry, schema_name
from .tool_dispatch_helpers import (
    is_multimodal_tool_result,
    make_tool_result_message,
    should_parallelize_tool_batch,
)
from .tool_guardrails import ToolCallGuardrailController, check_file_safety
from .tool_result_classification import file_mutation_result_landed
from .toolsets import disabled_backend_tool_names

__all__ = [
    "REGISTRY",
    "RESERVED_KEYS",
    "DESKTOP_ACTION_TOOL_NAMES",
    "WINDOW_ACTION_TOOL_NAMES",
    "ToolCallGuardrailController",
    "ToolsRegistry",
    "apply_search_tools_catalog",
    "check_file_safety",
    "coerce_tool_args",
    "disabled_backend_tool_names",
    "file_mutation_result_landed",
    "is_multimodal_tool_result",
    "make_tool_result_message",
    "schema_name",
    "search_domains_and_tools",
    "should_parallelize_tool_batch",
    "unavailable_presentation_tool_names",
]
