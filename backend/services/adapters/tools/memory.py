from components import MAX_AUTO_INJECT_CONTENT_CHARS

from services.domains.memory import AUTO_INJECT_SLOTS, RECALL_TAGS
from services.infrastructure.tool_runtime import ToolsRegistry

_RETAIN_DESC = (
    "Save a fact to long-term memory. Pick a kind based on whether the fact is "
    "background context that shapes every exchange (auto_inject) or a small "
    "fact you recall on demand (recall).\n"
    "  - auto_inject: upserts into one of {slots}. One row per slot — a second write overwrites. "
    "Capped at {auto_cap} chars; longer content is rejected at write time.\n"
    "  - recall: appended to a recall pool you must query via memory_recall(query=...) in future sessions. "
    "Requires a closed-set tag.\n"
    "Do not write project decisions or tool-chain facts as auto_inject — those "
    "should use recall with the appropriate tag."
).format(slots=", ".join(s.split(":", 1)[1] for s in AUTO_INJECT_SLOTS), auto_cap=MAX_AUTO_INJECT_CONTENT_CHARS)


RETAIN_SCHEMA = {
    "name": "memory_retain",
    "description": _RETAIN_DESC,
    "parameters": {
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "enum": ["recall", "auto_inject"],
                "description": (
                    "auto_inject for background context that's always in effect; "
                    "recall for everything else (default-friendly). Choose before "
                    "writing — the kind cannot be changed later."
                ),
            },
            "content": {"type": "string", "description": "The fact to remember. Keep it tight."},
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Required for kind='recall'. Pick ONE closed-set tag from: "
                    + ", ".join(sorted(RECALL_TAGS))
                    + ". Ignored for kind='auto_inject'."
                ),
            },
            "context": {
                "type": "string",
                "description": (
                    "For kind='recall': a short topic label like 'concise responses' "
                    "or 'repo layout'. Free-form but short.\n"
                    "For kind='auto_inject': MUST be one of " + ", ".join(AUTO_INJECT_SLOTS) + "."
                ),
            },
            "importance": {
                "type": "number",
                "description": "Optional importance factor (1.0 default, range 0.5 - 3.0). Higher values retain higher recall rank across time.",
            },
        },
        "required": ["kind", "content"],
    },
}

RECALL_SCHEMA = {
    "name": "memory_recall",
    "description": "Search recall-pool memories. Returns rows from kind='recall' only.",
    "parameters": {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "Keywords to search for in memory."}},
        "required": ["query"],
    },
}

FORGET_SCHEMA = {
    "name": "memory_forget",
    "description": "Delete a specific memory by its ID. Use this to remove outdated or incorrect facts.",
    "parameters": {
        "properties": {"memory_id": {"type": "integer", "description": "The ID of the memory to delete."}},
        "required": ["memory_id"],
    },
}


def register_memory_tools(registry: ToolsRegistry) -> None:
    """schema 唯一权威源；由 bootstrap 显式调用，memory 桶的执行在 chat 回合内的 NativeMemory。"""
    registry.register_memory("memory_retain", RETAIN_SCHEMA)
    registry.register_memory("memory_recall", RECALL_SCHEMA)
    registry.register_memory("memory_forget", FORGET_SCHEMA)
