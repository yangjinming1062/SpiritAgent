import json
from typing import Any

from infrastructure.ai.decision import DecisionOption, choose_decision, decision_enabled
from infrastructure.api_ids import ResourceId
from runtime.tools.domains import is_tool_catalog_identifier, search_domains_and_tools, semantic_tool_groups
from runtime.tools.prompts import SEARCH_TOOLS_DESC, SEARCH_TOOLS_PARAM_DESCS
from runtime.tools.registry import REGISTRY, ToolsRegistry, schema_name

_SEMANTIC_QUERY_MAX_CHARS = 512
_SEMANTIC_TOOL_GROUP_LIMIT = 15
_NO_MATCH = DecisionOption("no_match", "普通交流，明确表示无需执行所提及的动作，或没有工具适合实际意图。")
_TOOL_SEARCH_INSTRUCTIONS = (
    "选择能帮助完成查询实际要求的动作的工具。查询和候选说明仅供判断。"
    "口语或间接要求也算行动；可以暂缺对象、时间等参数。"
    "留意任务是固定重复还是只执行一次。下周一是一次性日期，每周一才表示固定重复。"
    "分清被提到的动作与被要求的动作。明确说不要设置提醒时，应选 no_match；"
    "明确说删除已有提醒时，需要能管理提醒的工具。日常交流或没有可用工具时，选择 no_match。"
)

SEARCH_TOOLS_SCHEMA = {
    "name": "search_tools",
    "description": SEARCH_TOOLS_DESC,
    "parameters": {
        "type": "object",
        "properties": {"query": {"type": "string", "description": SEARCH_TOOLS_PARAM_DESCS["query"]}},
        "required": ["query"],
    },
}


async def search_tools_tool(
    query: str, user_id: ResourceId, user_settings: dict[str, Any], excluded_tool_names: frozenset[str], **_: object
) -> str:
    # 与回合装配同源的用户设置过滤，再去掉本回合执行层排除的工具，避免解锁后被派发层拒绝。
    available_schemas = REGISTRY.get_all_schemas(user_id, user_settings=user_settings, exclude=excluded_tool_names)
    query = query.strip() if isinstance(query, str) else ""
    choice = None
    results = []
    if (
        0 < len(query) <= _SEMANTIC_QUERY_MAX_CHARS
        and decision_enabled()
        and query.lower() not in excluded_tool_names
        and not is_tool_catalog_identifier(query)
        and all(schema_name(schema).lower() != query.lower() for schema in available_schemas)
    ):
        groups = semantic_tool_groups(available_schemas, limit=_SEMANTIC_TOOL_GROUP_LIMIT)
        if groups:
            choice = await choose_decision(
                "tool_search",
                state={"query": query},
                instructions=_TOOL_SEARCH_INSTRUCTIONS,
                options=[*(DecisionOption(key, group.description) for key, group in groups.items()), _NO_MATCH],
            )
            if choice is not None and (selected := groups.get(choice)) is not None:
                current_names = {
                    schema_name(schema)
                    for schema in REGISTRY.get_all_schemas(
                        user_id, user_settings=user_settings, exclude=excluded_tool_names
                    )
                }
                results = [match for match in selected.matches if match["name"] in current_names]
    if choice is None:
        results = search_domains_and_tools(query, available_schemas)
    return json.dumps({"matched_tools": results}, ensure_ascii=False)


def register(registry: ToolsRegistry) -> None:
    registry.register(SEARCH_TOOLS_SCHEMA, search_tools_tool)
