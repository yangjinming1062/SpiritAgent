from features.memory.memory_policy import MemoryDecisions
from features.memory.prompts import MEMORY_POLICY
from runtime.tools.prompts import MEMORY_INSPECT_DESC, MEMORY_RECALL_DESC, MEMORY_RECALL_PARAM_DESCS, MEMORY_RETAIN_DESC
from runtime.tools.registry import ToolsRegistry

RETAIN_SCHEMA = {
    "name": "memory_retain",
    "description": MEMORY_RETAIN_DESC,
    "parameters": MemoryDecisions.model_json_schema(),
}
RECALL_SCHEMA = {
    "name": "memory_recall",
    "description": MEMORY_RECALL_DESC,
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": MEMORY_RECALL_PARAM_DESCS["query"]},
            "diary_date": {
                "type": "string",
                "format": "date",
                "description": MEMORY_RECALL_PARAM_DESCS["diary_date"],
            },
        },
    },
}
INSPECT_SCHEMA = {
    "name": "memory_inspect",
    "description": MEMORY_INSPECT_DESC + "\n\n" + MEMORY_POLICY,
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "before_memory_id": {"type": "string", "pattern": "^[1-9][0-9]*$"},
        },
    },
}


def register_memory_tools(registry: ToolsRegistry) -> None:
    for schema in (RETAIN_SCHEMA, RECALL_SCHEMA, INSPECT_SCHEMA):
        registry.register_memory(schema)
