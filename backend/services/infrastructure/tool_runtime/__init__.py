"""工具运行时基础设施：注册表、执行防护、参数矫正与工具结果处理。"""

from .domains import search_domains_and_tools
from .model_tools import coerce_tool_args
from .registry import REGISTRY, RESERVED_KEYS, ToolsRegistry, schema_name
from .tool_dispatch_helpers import (
    is_multimodal_tool_result,
    make_tool_result_message,
    should_parallelize_tool_batch,
)
from .tool_guardrails import ToolCallGuardrailController, check_file_safety
from .tool_result_classification import file_mutation_result_landed

__all__ = [
    "REGISTRY",
    "RESERVED_KEYS",
    "ToolCallGuardrailController",
    "ToolsRegistry",
    "check_file_safety",
    "coerce_tool_args",
    "file_mutation_result_landed",
    "is_multimodal_tool_result",
    "make_tool_result_message",
    "schema_name",
    "search_domains_and_tools",
    "should_parallelize_tool_batch",
]
