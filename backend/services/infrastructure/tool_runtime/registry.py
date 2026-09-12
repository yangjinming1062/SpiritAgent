import inspect
import json
from collections.abc import Callable
from typing import Any

from components import get_logger, tool_error

from services.contracts.delegation import DelegateAction

from .domains import apply_search_tools_catalog
from .toolsets import disabled_backend_tool_names

logger = get_logger(__name__)


def schema_name(schema: dict[str, Any]) -> str:
    return schema.get("name", "")


# 工具的 ``user_id`` / ``llm_config`` / ``user_settings`` 是正常 kwargs，被注入时从 LLM 提供的 args 中静默剔除，避免受 LLM 注入。
RESERVED_KEYS = frozenset(
    {
        "user_id",
        "llm_config",
        "user_settings",
        "system_preset_id",
        "scope",
        "memory_scope",
        "source_kind",
        "source_refs",
        "content_version",
        "parent_session_id",
        "emitter",
        "skill_scope",
    },
)

# 判定只读进程级配置，与调用用户无关。
AvailabilityCheck = Callable[[], bool]


class ToolsRegistry:
    """三个桶：backend（进程内）、memory（DB）、runner（按用户 IPC）。"""

    def __init__(self) -> None:
        self._backend_tools: dict[str, dict[str, Any]] = {}
        self._memory_tools: dict[str, dict[str, Any]] = {}
        self._runner_tools: dict[int, dict[str, dict[str, Any]]] = {}

    def register(
        self,
        name: str,
        schema: dict,
        func: Callable,
        availability_check: AvailabilityCheck | None = None,
    ) -> None:
        """添加（或替换）一个 backend 工具；导入时幂等，重复 ``import`` 不会报错。"""
        self._backend_tools[name] = {
            "schema": schema,
            "func": func,
            "is_coro": inspect.iscoroutinefunction(func),
            "availability_check": availability_check,
        }

    def register_memory(self, name: str, schema: dict) -> None:
        """添加 memory 工具（无函数——由 ``NativeMemory`` 派发），以扁平 ``{name: schema}`` 存储。"""
        self._memory_tools[name] = schema

    def update_runner_tools(self, user_id: int, schemas: list[dict[str, Any]], *, skill_scope_version: int = 0) -> None:
        self._runner_tools[user_id] = {
            schema_name(schema): schema
            for schema in schemas
            if skill_scope_version == 1 or schema_name(schema) not in {"skills_list", "skill_view", "skill_manage"}
        }
        logger.info("Updated runner tools", extra={"user_id": user_id, "schema_count": len(schemas)})

    def clear_runner_tools(self, user_id: int) -> None:
        if user_id in self._runner_tools:
            del self._runner_tools[user_id]
            logger.info("Cleared runner tools", extra={"user_id": user_id})

    def has_runner_tools(self, user_id: int) -> bool:
        return bool(self._runner_tools.get(user_id))

    def get_all_schemas(self, user_id: int, user_settings: dict[str, str]) -> list[dict[str, Any]]:
        # 谓词抛错则隐藏该工具（fail-closed），避免一个 bug 把整次调用拖到 500。toolsets.disabled 对 backend/memory 桶生效；runner 桶在客户端 get_tools 源头已按同一键过滤。
        excluded = disabled_backend_tool_names(user_settings)
        schemas: list[dict[str, Any]] = []
        for entry in self._backend_tools.values():
            if schema_name(entry["schema"]) in excluded:
                continue
            try:
                if (check := entry["availability_check"]) is None or check():
                    schemas.append(entry["schema"])
            except Exception as e:
                logger.warning(
                    "availability_check raised; hiding tool",
                    extra={"tool_name": schema_name(entry["schema"]), "error_msg": str(e)},
                )
        schemas.extend(s for s in self._memory_tools.values() if schema_name(s) not in excluded)
        schemas.extend(self._runner_tools.get(user_id, {}).values())
        return apply_search_tools_catalog(schemas)

    def _lookup(self, user_id: int, tool_name: str) -> tuple[str, dict[str, Any]] | None:
        if (entry := self._backend_tools.get(tool_name)) is not None:
            return "backend", entry
        if (schema := self._memory_tools.get(tool_name)) is not None:
            return "memory", {"schema": schema}
        if (entry := self._runner_tools.get(user_id, {}).get(tool_name)) is not None:
            return "runner", {"schema": entry}
        return None

    def get_schema(self, user_id: int, tool_name: str) -> dict[str, Any] | None:
        return found[1]["schema"] if (found := self._lookup(user_id, tool_name)) else None

    def get_location(self, user_id: int, tool_name: str) -> str:
        return found[0] if (found := self._lookup(user_id, tool_name)) else "unknown"

    async def execute_backend_tool(self, name: str, args: dict, **context) -> str | DelegateAction:
        entry = self._backend_tools.get(name)
        if entry is None:
            return tool_error(f"Tool {name} not found in backend registry.")

        # 保留键始终优先——见模块顶部 RESERVED_KEYS。
        call_args: dict[str, Any] = {k: v for k, v in (args or {}).items() if k not in RESERVED_KEYS}
        for k, v in context.items():
            call_args.setdefault(k, v)

        func = entry["func"]
        try:
            result = await func(**call_args) if entry["is_coro"] else func(**call_args)
        except Exception as e:
            logger.error("Error executing backend tool", extra={"tool_name": name, "error": str(e)})
            return tool_error(str(e))

        # DelegateAction 是控制动作不是结果，原样交回调用方（对话执行层）接管。
        return result if isinstance(result, (str, DelegateAction)) else json.dumps(result, ensure_ascii=False)


REGISTRY = ToolsRegistry()
