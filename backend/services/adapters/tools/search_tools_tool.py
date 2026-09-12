import json

from services.infrastructure.tool_runtime.domains import search_domains_and_tools
from services.infrastructure.tool_runtime.registry import REGISTRY

SEARCH_TOOLS_SCHEMA = {
    "name": "search_tools",
    "description": "Search by domain or intent and unlock matching tools for immediate use.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Domain id (e.g. files, browser) or intent (e.g. 读文件, run python).",
            },
        },
        "required": ["query"],
    },
}


def search_tools_tool(query: str, **kwargs) -> str:
    user_id = kwargs.get("user_id")
    if user_id is None:
        # 由 ToolsRegistry.execute_backend_tool 注入保留键；直接单元测试调用时缺失，返回空结果而非崩溃。
        return json.dumps({"matched_tools": []}, ensure_ascii=False)

    # 与 build_turn_inputs 同源过滤——否则会向 LLM 暴露受门控的 schema（如未配置 Tavily key 的 web_extract）。
    user_settings = kwargs.get("user_settings") or {}
    available_schemas = REGISTRY.get_all_schemas(user_id, user_settings=user_settings)
    results = search_domains_and_tools(query if isinstance(query, str) else "", available_schemas)
    return json.dumps({"matched_tools": results}, ensure_ascii=False)


def register(registry) -> None:
    REGISTRY.register("search_tools", SEARCH_TOOLS_SCHEMA, search_tools_tool)
