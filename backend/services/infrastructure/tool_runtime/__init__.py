"""工具运行时基础设施：注册表、执行防护、参数矫正与工具结果处理。"""

from .domains import (
    DOMAIN_CATALOG,
    ToolDomain,
    apply_search_tools_catalog,
    resolve_tools_for_domain,
    search_domains_and_tools,
)
from .file_safety import get_read_block_error, is_write_denied
from .model_tools import coerce_tool_args
from .registry import (
    REGISTRY,
    RESERVED_KEYS,
    ToolsRegistry,
    schema_name,
)
from .tool_dispatch_helpers import (
    is_multimodal_tool_result,
    make_tool_result_message,
    should_parallelize_tool_batch,
)
from .tool_guardrails import (
    IDEMPOTENT_TOOL_NAMES,
    MUTATING_TOOL_NAMES,
    ToolCallGuardrailConfig,
    ToolCallGuardrailController,
    ToolCallSignature,
    ToolGuardrailDecision,
    append_toolguard_guidance,
    canonical_tool_args,
    check_file_safety,
    classify_tool_failure,
    toolguard_synthetic_result,
)
from .tool_result_classification import (
    FILE_MUTATING_TOOL_NAMES,
    file_mutation_result_landed,
)
from .toolsets import TOOLSET_CATALOG, ToolsetDef, disabled_backend_tool_names

__all__ = [
    "DOMAIN_CATALOG",
    "FILE_MUTATING_TOOL_NAMES",
    "IDEMPOTENT_TOOL_NAMES",
    "MUTATING_TOOL_NAMES",
    "REGISTRY",
    "RESERVED_KEYS",
    "TOOLSET_CATALOG",
    "ToolCallGuardrailConfig",
    "ToolCallGuardrailController",
    "ToolCallSignature",
    "ToolDomain",
    "ToolGuardrailDecision",
    "ToolsetDef",
    "ToolsRegistry",
    "append_toolguard_guidance",
    "apply_search_tools_catalog",
    "canonical_tool_args",
    "check_file_safety",
    "classify_tool_failure",
    "coerce_tool_args",
    "disabled_backend_tool_names",
    "file_mutation_result_landed",
    "get_read_block_error",
    "is_multimodal_tool_result",
    "is_write_denied",
    "make_tool_result_message",
    "resolve_tools_for_domain",
    "schema_name",
    "search_domains_and_tools",
    "should_parallelize_tool_batch",
    "toolguard_synthetic_result",
]
