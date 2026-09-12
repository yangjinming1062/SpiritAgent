"""工具运行时基础设施：注册表、执行防护、参数矫正与工具结果处理。"""

from services.infrastructure.tool_runtime.domains import (
    DOMAIN_CATALOG,
    ToolDomain,
    apply_search_tools_catalog,
    format_available_domain_lines,
    resolve_tools_for_domain,
    search_domains_and_tools,
)
from services.infrastructure.tool_runtime.file_safety import (
    BLOCKED_PROJECT_ENV_BASENAMES,
    SPIRITAGENT_CONTROL_FILE_BASENAMES,
    get_read_block_error,
    is_write_denied,
)
from services.infrastructure.tool_runtime.model_tools import coerce_tool_args
from services.infrastructure.tool_runtime.registry import (
    REGISTRY,
    RESERVED_KEYS,
    AvailabilityCheck,
    ToolsRegistry,
    schema_name,
)
from services.infrastructure.tool_runtime.tool_dispatch_helpers import (
    is_multimodal_tool_result,
    make_tool_result_message,
    should_parallelize_tool_batch,
)
from services.infrastructure.tool_runtime.tool_guardrails import (
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
from services.infrastructure.tool_runtime.tool_result_classification import (
    FILE_MUTATING_TOOL_NAMES,
    file_mutation_result_landed,
)
from services.infrastructure.tool_runtime.toolsets import TOOLSET_CATALOG, ToolsetDef, disabled_backend_tool_names

__all__ = [
    "BLOCKED_PROJECT_ENV_BASENAMES",
    "DOMAIN_CATALOG",
    "FILE_MUTATING_TOOL_NAMES",
    "IDEMPOTENT_TOOL_NAMES",
    "MUTATING_TOOL_NAMES",
    "REGISTRY",
    "RESERVED_KEYS",
    "SPIRITAGENT_CONTROL_FILE_BASENAMES",
    "TOOLSET_CATALOG",
    "ToolCallGuardrailConfig",
    "ToolCallGuardrailController",
    "ToolCallSignature",
    "ToolDomain",
    "ToolGuardrailDecision",
    "ToolsetDef",
    "ToolsRegistry",
    "AvailabilityCheck",
    "append_toolguard_guidance",
    "apply_search_tools_catalog",
    "canonical_tool_args",
    "check_file_safety",
    "classify_tool_failure",
    "coerce_tool_args",
    "disabled_backend_tool_names",
    "file_mutation_result_landed",
    "format_available_domain_lines",
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
