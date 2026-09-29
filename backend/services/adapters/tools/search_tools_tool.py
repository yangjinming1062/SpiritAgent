import json
from typing import Any

from prompts.tools import SEARCH_TOOLS_DESC, SEARCH_TOOLS_PARAM_DESCS

from services.infrastructure.tool_runtime import REGISTRY, ToolsRegistry, schema_name, search_domains_and_tools

SEARCH_TOOLS_SCHEMA = {
    "name": "search_tools",
    "description": SEARCH_TOOLS_DESC,
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": SEARCH_TOOLS_PARAM_DESCS["query"],
            },
        },
        "required": ["query"],
    },
}


async def search_tools_tool(
    query: str,
    user_id: int,
    user_settings: dict[str, Any],
    excluded_tool_names: frozenset[str],
    **_: object,
) -> str:
    # 与回合装配同源的用户设置过滤，再去掉本回合执行层排除的工具，避免解锁后被派发层拒绝。
    available_schemas = [
        schema
        for schema in REGISTRY.get_all_schemas(user_id, user_settings=user_settings)
        if schema_name(schema) not in excluded_tool_names
    ]
    results = search_domains_and_tools(query if isinstance(query, str) else "", available_schemas)
    return json.dumps({"matched_tools": results}, ensure_ascii=False)


def register(registry: ToolsRegistry) -> None:
    registry.register(SEARCH_TOOLS_SCHEMA, search_tools_tool)
