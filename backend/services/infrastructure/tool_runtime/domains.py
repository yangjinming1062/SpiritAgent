"""工具业务域目录与渐进式检索索引。

业务域短描述写入元工具 Schema，供模型按域或意图检索并就地解锁完整工具 Schema。
系统提示词不拼装这份清单，以免每回合破坏前缀缓存。
"""

import re
from dataclasses import dataclass
from typing import Any

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


DOMAIN_CATALOG: tuple[ToolDomain, ...] = (
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
        description_zh="长期记忆与日记",
        description_en="Long-term memory and journal",
        aliases=("memory", "diary", "moments", "记忆", "日记", "时刻", "回忆", "记住", "备忘"),
        extra_tools=("memory_retain", "memory_recall", "memory_inspect", "moment_create", "diary_write"),
    ),
    ToolDomain(
        id="media",
        description_zh="生图、视频与房间背景",
        description_en="Images, video, and room backdrop",
        aliases=(
            "media",
            "image_generation",
            "image",
            "picture",
            "video",
            "生图",
            "画图",
            "图片",
            "视频",
            "房间背景",
            "背景",
        ),
        extra_tools=("image_generate", "room_backdrop_update", "video_generate", "video_generate_status"),
    ),
    ToolDomain(
        id="code",
        description_zh="沙箱执行 Python",
        description_en="Run Python in a sandbox",
        aliases=("code", "code_execution", "python", "代码", "代码执行", "运行代码", "脚本计算", "沙箱"),
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
        description_zh="查看或结束进程",
        description_en="List or kill processes",
        aliases=("process", "process_management", "进程", "任务管理器", "查进程", "杀进程", "系统进程"),
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
        description_zh="定时任务",
        description_en="Scheduled tasks",
        aliases=("tasks", "scheduled_tasks", "cron", "定时", "定时任务", "周期任务", "闹钟", "提醒", "定时器"),
        extra_tools=("cronjob",),
    ),
    ToolDomain(
        id="computer_use",
        description_zh="屏幕视觉与键鼠",
        description_en="Screen vision and input",
        aliases=("computer_use", "vision_analyze", "视觉自动化", "桌面控制", "屏幕点击", "自动操作", "视觉定位"),
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
        if catalog:
            updated["description"] = (
                "按业务域或意图检索并解锁工具；匹配项会立即加入活动列表。"
                " Search by domain or intent to unlock tools for immediate use.\n"
                f"可用业务域 / available domains:\n{catalog}"
            )
        else:
            updated["description"] = (
                "按业务域或意图检索并解锁工具。当前没有可检索的业务域。 Search by domain or intent to unlock tools. No domains are currently available."
            )
        decorated.append(updated)
    return decorated


def search_domains_and_tools(query: str, available_schemas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    query_str = query.strip().lower()
    if not query_str:
        return []

    schema_map: dict[str, dict[str, Any]] = {
        name: schema for schema in available_schemas if (name := schema.get("name")) and isinstance(name, str)
    }
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

    matched_names.discard(_META_TOOL_NAME)
    return [
        {"name": name, "description": schema_map[name].get("description", "")}
        for name in sorted(matched_names)
        if name in schema_map
    ]
