from services.domains.memory import MemoryDecisions
from services.infrastructure.tool_runtime import ToolsRegistry

RETAIN_SCHEMA = {
    "name": "memory_retain",
    "description": "Propose an atomic batch of evidence-grounded memory changes after memory_inspect. An independent LLM review may reject or revise it. Revise or invalidate incorrect facts using their ID and version. Never ask the user to approve maintenance.",
    "parameters": MemoryDecisions.model_json_schema(),
}
RECALL_SCHEMA = {
    "name": "memory_recall",
    "description": "Search active, unexpired memories for conversational use. Basis and scope qualify every result.",
    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
}
INSPECT_SCHEMA = {
    "name": "memory_inspect",
    "description": "Inspect original conversation evidence and memory versions before maintenance. Candidates and invalidated claims are NOT user facts and must not inform conversation. Optional query searches text; before_memory_id pages through older records.",
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
