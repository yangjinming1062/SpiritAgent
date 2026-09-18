from prompts.memory import MEMORY_POLICY
from prompts.tools import MEMORY_INSPECT_DESC, MEMORY_RECALL_DESC, MEMORY_RETAIN_DESC

from services.domains.memory import MemoryDecisions
from services.infrastructure.tool_runtime import ToolsRegistry

RETAIN_SCHEMA = {
    "name": "memory_retain",
    "description": MEMORY_RETAIN_DESC,
    "parameters": MemoryDecisions.model_json_schema(),
}
RECALL_SCHEMA = {
    "name": "memory_recall",
    "description": MEMORY_RECALL_DESC,
    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
}
INSPECT_SCHEMA = {
    "name": "memory_inspect",
    "description": MEMORY_INSPECT_DESC + "\n\n" + MEMORY_POLICY,
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "before_memory_id": {"type": "integer", "minimum": 1},
        },
    },
}


def register_memory_tools(registry: ToolsRegistry) -> None:
    for schema in (RETAIN_SCHEMA, RECALL_SCHEMA, INSPECT_SCHEMA):
        registry.register_memory(schema["name"], schema)
