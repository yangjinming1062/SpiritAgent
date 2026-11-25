"""工具业务域目录与渐进式检索索引。业务域短描述写入元工具 Schema，供模型按域或意图检索并就地解锁完整工具 Schema；系统提示词不拼装这份清单，以免每回合破坏前缀缓存。"""

import re
from dataclasses import dataclass
from typing import Any, TypedDict

from runtime.tools.prompts import SEARCH_TOOLS_CATALOG_DESC, SEARCH_TOOLS_DESC

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_META_TOOL_NAME = "search_tools"


@dataclass(frozen=True)
class ToolDomain:
    id: str
    description_zh: str
    description_en: str
    aliases: tuple[str, ...]
    prefixes: tuple[str, ...] = ()
    extra_tools: tuple[str, ...] = ()


class ToolSearchMatch(TypedDict):
    name: str
    description: str


@dataclass(frozen=True)
class ToolSearchGroup:
    description: str
    matches: tuple[ToolSearchMatch, ...]


DOMAIN_CATALOG: tuple[ToolDomain, ...] = (
    ToolDomain(
        id="life",
        description_zh="伙伴的持续事项、生活历史、阅读所得与日记",
        description_en="Companion pursuits, life history, reading notes and diaries",
        aliases=("life", "diary", "生活", "经历", "事项", "计划", "暂停", "放弃", "日记"),
        extra_tools=("life_inspect", "life_adjust", "life_share_work", "life_recall", "diary_recall"),
    ),
    ToolDomain(
        id="browser",
        description_zh="浏览与操作网页",
        description_en="Browse and automate web pages",
        aliases=("browser", "web_browser", "chrome", "edge", "网页", "浏览器", "上网", "抓取页面", "访问网页"),
        prefixes=("browser_",),
    ),
    ToolDomain(
        id="files",
        description_zh="读写本地文件",
        description_en="Read and write local files",
        aliases=("files", "filesystem", "file_operations", "文件", "目录", "读写", "代码文件", "补丁", "查文件"),
        extra_tools=("read_file", "write_file", "patch", "list_directory", "search_files"),
    ),
    ToolDomain(
        id="terminal",
        description_zh="运行本机命令",
        description_en="Run local shell commands",
        aliases=("terminal", "shell", "bash", "cmd", "powershell", "终端", "命令行", "运行命令", "执行命令", "控制台"),
        extra_tools=("terminal",),
    ),
    ToolDomain(
        id="web",
        description_zh="搜索与抓取网页",
        description_en="Search the web and extract pages",
        aliases=("web", "web_tools", "internet", "搜索", "联网", "网页搜索", "查资料", "搜索引擎"),
        extra_tools=("web_search", "web_extract"),
    ),
    ToolDomain(
        id="memory",
        description_zh="查找从前聊过的内容、经历或偏好，管理长期记忆",
        description_en="Recall past conversations, experiences or preferences and manage long-term memory",
        aliases=("memory", "reflection", "记忆", "反思", "相处", "回忆", "记住", "备忘"),
        extra_tools=("memory_retain", "memory_recall", "memory_inspect"),
    ),
    ToolDomain(
        id="media",
        description_zh="图片与视频生成",
        description_en="Image and video generation",
        aliases=("media", "image_generation", "image", "picture", "video", "生图", "画图", "图片", "视频"),
        extra_tools=("image_generate", "media_inspect", "image_regenerate", "video_generate", "video_generate_status"),
    ),
    ToolDomain(
        id="scenes",
        description_zh="生活场景与环境图片",
        description_en="Life scenes and environment pictures",
        aliases=("scenes", "scene", "场景", "环境", "背景"),
        extra_tools=("scene_list", "scene_get", "scene_create"),
    ),
    ToolDomain(
        id="actions",
        description_zh="动作与图片、视频成品",
        description_en="Actions and finished pictures or videos",
        aliases=("actions", "action", "动作", "表演", "动作库", "活动", "影像", "visual"),
        extra_tools=(
            "action_search",
            "action_design",
            "action_inspect",
            "visual_search",
            "visual_inspect",
            "visual_generate",
            "outfit_list",
            "visual_play",
        ),
    ),
    ToolDomain(
        id="code",
        description_zh="在用户电脑上执行 Python 代码（不是隔离沙箱）",
        description_en="Run Python code on the user's computer (not an isolated sandbox)",
        aliases=("code", "code_execution", "python", "代码", "代码执行", "运行代码", "脚本计算"),
        extra_tools=("execute_code",),
    ),
    ToolDomain(
        id="system",
        description_zh="系统窗口与屏幕状态",
        description_en="OS windows and screen state",
        aliases=("system", "system_awareness", "系统", "屏幕", "窗口", "鼠标", "桌面状态", "状态感知"),
        prefixes=("system.",),
    ),
    ToolDomain(
        id="process",
        description_zh="查看或结束终端后台任务",
        description_en="List or stop background terminal sessions",
        aliases=("process", "process_management", "进程", "后台任务", "后台进程", "查进程"),
        extra_tools=("process",),
    ),
    ToolDomain(
        id="skills",
        description_zh="技能手册与管理",
        description_en="Skill manuals and management",
        aliases=("skills", "skills_system", "skill", "技能", "工作流", "技能库", "操作手册"),
        extra_tools=("skills_list", "skill_view", "skill_manage"),
    ),
    ToolDomain(
        id="tasks",
        description_zh="安排或管理在指定时间执行的一次性或周期性任务，以及稍后的联系意图",
        description_en="Arrange or manage one-time or recurring scheduled tasks, and follow-up contact intents",
        aliases=("tasks", "scheduled_tasks", "cron", "定时", "定时任务", "周期任务", "闹钟", "提醒", "定时器"),
        extra_tools=("cronjob", "companion_wait"),
    ),
    ToolDomain(
        id="computer_use",
        description_zh="屏幕视觉、图片理解与键鼠",
        description_en="Screen vision, image understanding and input",
        aliases=(
            "computer_use",
            "vision_analyze",
            "视觉自动化",
            "桌面控制",
            "屏幕点击",
            "自动操作",
            "视觉定位",
            "看图",
            "识别图片",
            "图片理解",
        ),
        extra_tools=("computer_use", "vision_analyze"),
    ),
    ToolDomain(
        id="agent",
        description_zh="子智能体与发消息",
        description_en="Delegate to agents and message",
        aliases=("agent", "agent_delegation", "messaging", "子任务", "委派", "智能体", "协作", "发消息"),
        extra_tools=("agent_delegate_tool", "send_message_tool"),
    ),
)


def resolve_tools_for_domain(domain: ToolDomain, available_tool_names: set[str]) -> list[str]:
    return [
        name
        for name in sorted(available_tool_names)
        if (domain.prefixes and any(name.startswith(p) for p in domain.prefixes)) or name in domain.extra_tools
    ]


def _has_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text))


def _ascii_token_match(token: str, query_str: str) -> bool:
    return re.search(rf"(?<![a-z0-9_]){re.escape(token)}(?![a-z0-9_])", query_str) is not None


def _domain_matches(domain: ToolDomain, query_str: str) -> bool:
    if query_str == domain.id.lower() or _ascii_token_match(domain.id.lower(), query_str):
        return True
    for alias in domain.aliases:
        alias_l = alias.lower()
        if query_str == alias_l:
            return True
        if _has_cjk(alias):
            if alias_l in query_str:
                return True
        elif _ascii_token_match(alias_l, query_str):
            return True
    return False


def format_available_domain_lines(available_tool_names: set[str]) -> str:
    lines = [
        f"- {domain.id}: {domain.description_zh} / {domain.description_en}"
        for domain in DOMAIN_CATALOG
        if resolve_tools_for_domain(domain, available_tool_names)
    ]
    return "\n".join(lines)


def apply_search_tools_catalog(schemas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    available_names = {name for schema in schemas if (name := schema.get("name")) and isinstance(name, str)}
    catalog = format_available_domain_lines(available_names)
    decorated: list[dict[str, Any]] = []
    for schema in schemas:
        if schema.get("name") != _META_TOOL_NAME:
            decorated.append(schema)
            continue
        updated = dict(schema)
        updated["description"] = SEARCH_TOOLS_CATALOG_DESC.format(catalog=catalog) if catalog else SEARCH_TOOLS_DESC
        decorated.append(updated)
    return decorated


def _tool_summary(description: object) -> str:
    """检索结果只给首句说明；匹配的工具已就地解锁，完整描述随下一次请求的 Schema 提供，不在结果里重复。"""
    text = description.strip() if isinstance(description, str) else ""
    return re.split(r"(?<=[。.!?])\s*|\n", text, maxsplit=1)[0][:160]


def _searchable_schemas(available_schemas: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        name: schema
        for schema in available_schemas
        if (name := schema.get("name")) and isinstance(name, str) and name != _META_TOOL_NAME
    }


def _search_match(name: str, schema: dict[str, Any]) -> ToolSearchMatch:
    return ToolSearchMatch(name=name, description=_tool_summary(schema.get("description")))


def is_tool_catalog_identifier(query: str) -> bool:
    identifier = query.lower()
    if not re.fullmatch(r"[a-z0-9_.-]+", identifier):
        return False
    return identifier == _META_TOOL_NAME or any(
        identifier == domain.id or identifier in domain.extra_tools or identifier.startswith(domain.prefixes)
        for domain in DOMAIN_CATALOG
    )


_SEMANTIC_TOOL_DESCRIPTIONS = {
    "cronjob": "只管理固定重复执行的任务，例如每天或每周一都执行。支持创建、查询、改期、暂停和删除这类周期任务。",
    "companion_wait": "只安排一次的伙伴联系或提醒，例如下周一早上提醒一次。支持查询和取消已有的一次性提醒。",
}


def _semantic_tool_description(match: ToolSearchMatch) -> str:
    return _SEMANTIC_TOOL_DESCRIPTIONS.get(match["name"], f"{match['name']}: {match['description']}")


def semantic_tool_groups(available_schemas: list[dict[str, Any]], *, limit: int) -> dict[str, ToolSearchGroup]:
    """小目录直接选工具，大目录只提供当前有工具的业务域。"""
    schemas = _searchable_schemas(available_schemas)
    if len(schemas) <= limit:
        matches = [_search_match(name, schemas[name]) for name in sorted(schemas)]
        return {
            f"tool_{match['name']}": ToolSearchGroup(_semantic_tool_description(match), (match,)) for match in matches
        }

    names = set(schemas)
    groups: dict[str, ToolSearchGroup] = {}
    for domain in DOMAIN_CATALOG:
        domain_names = resolve_tools_for_domain(domain, names)
        if not domain_names:
            continue
        matches = tuple(_search_match(name, schemas[name]) for name in domain_names)
        if len(matches) == 1 or not domain.prefixes and len(matches) < len(domain.extra_tools):
            description = " / ".join(_semantic_tool_description(match) for match in matches)
        else:
            description = domain.description_zh
        # 终端执行和终端后台进程共用选项，为“无匹配”保留一个位置。
        key = "terminal" if domain.id == "process" else domain.id
        if previous := groups.get(key):
            description = f"{previous.description} / {description}"
            matches = (*previous.matches, *matches)
        groups[key] = ToolSearchGroup(description, matches)

    return groups if len(groups) <= limit else {}


def search_domains_and_tools(query: str, available_schemas: list[dict[str, Any]]) -> list[ToolSearchMatch]:
    query_str = query.strip().lower()
    if not query_str:
        return []

    schema_map = _searchable_schemas(available_schemas)
    available_names = set(schema_map)
    matched_names: set[str] = set()

    for domain in DOMAIN_CATALOG:
        if _domain_matches(domain, query_str):
            matched_names.update(resolve_tools_for_domain(domain, available_names))

    for name, schema in schema_map.items():
        if name in matched_names:
            continue
        desc = schema.get("description", "")
        desc_match = isinstance(desc, str) and len(query_str) >= 2 and query_str in desc.lower()
        if query_str in name.lower() or desc_match:
            matched_names.add(name)

    return [_search_match(name, schema_map[name]) for name in sorted(matched_names)]
