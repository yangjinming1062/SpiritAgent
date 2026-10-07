import inspect
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from components import get_logger, redact_sensitive_text, tool_error
from sqlalchemy.exc import SQLAlchemyError

from services.contracts import DelegateAction
from services.infrastructure.desktop import MANAGER

from .domains import apply_search_tools_catalog
from .toolsets import disabled_backend_tool_names

logger = get_logger(__name__)


def schema_name(schema: dict[str, Any]) -> str:
    return schema.get("name", "")


# 服务端注入的运行上下文键：派发入口先从模型参数中剔除同名键，避免模型覆盖身份、配置与作用域。
RESERVED_KEYS = frozenset(
    {
        "user_id",
        "memory_scope",
        "tool_call_id",
        "proactive_turn",
        "user_message",
        "llm_config",
        "user_settings",
        "scene_turn",
        "media_turn",
        "system_preset_id",
        "scope",
        "source_kind",
        "source_refs",
        "content_version",
        "parent_session_id",
        "excluded_tool_names",
        "skill_scope",
    },
)

# 判定只读进程级配置，与调用用户无关。
AvailabilityCheck = Callable[[], bool]
ToolLocation = Literal["backend", "memory", "runner"]


@dataclass(frozen=True)
class _BackendTool:
    schema: dict[str, Any]
    func: Callable[..., Any]
    availability_check: AvailabilityCheck | None


class ToolsRegistry:
    """三个桶：backend（进程内函数）、memory（由 NativeMemory 派发，只存 schema）、runner（按用户同步的本机工具）。"""

    def __init__(self) -> None:
        self._backend_tools: dict[str, _BackendTool] = {}
        self._memory_tools: dict[str, dict[str, Any]] = {}
        self._runner_tools: dict[int, dict[str, dict[str, Any]]] = {}

    def register(
        self,
        schema: dict[str, Any],
        func: Callable[..., Any],
        availability_check: AvailabilityCheck | None = None,
    ) -> None:
        """按 ``schema["name"]`` 添加或替换 backend 工具；重复注册幂等。"""
        self._backend_tools[schema["name"]] = _BackendTool(schema, func, availability_check)

    def register_memory(self, schema: dict[str, Any]) -> None:
        self._memory_tools[schema["name"]] = schema

    def update_runner_tools(self, user_id: int, schemas: list[dict[str, Any]]) -> None:
        self._runner_tools[user_id] = {schema_name(schema): schema for schema in schemas}
        logger.info("Updated runner tools", extra={"user_id": user_id, "schema_count": len(schemas)})

    def clear_runner_tools(self, user_id: int) -> None:
        if self._runner_tools.pop(user_id, None) is not None:
            logger.info("Cleared runner tools", extra={"user_id": user_id})

    def has_runner_tools(self, user_id: int) -> bool:
        return MANAGER.is_connected(user_id) and bool(self._runner_tools.get(user_id))

    def get_all_schemas(self, user_id: int, user_settings: dict[str, Any]) -> list[dict[str, Any]]:
        # 谓词抛错则隐藏该工具（fail-closed），避免一个 bug 把整次调用拖到 500。toolsets.disabled 对 backend/memory 桶生效；runner 桶在客户端 get_tools 源头已按同一键过滤
        excluded = disabled_backend_tool_names(user_settings)
        schemas: list[dict[str, Any]] = []
        for name, tool in self._backend_tools.items():
            if name in excluded:
                continue
            try:
                if tool.availability_check is None or tool.availability_check():
                    schemas.append(tool.schema)
            except Exception as e:
                logger.warning("availability_check raised; hiding tool", extra={"tool_name": name, "error_msg": str(e)})
        schemas.extend(schema for name, schema in self._memory_tools.items() if name not in excluded)
        if MANAGER.is_connected(user_id):
            schemas.extend(self._runner_tools.get(user_id, {}).values())
        return apply_search_tools_catalog(schemas)

    def _lookup(self, user_id: int, tool_name: str) -> tuple[ToolLocation, dict[str, Any]] | None:
        if (tool := self._backend_tools.get(tool_name)) is not None:
            return "backend", tool.schema
        if (schema := self._memory_tools.get(tool_name)) is not None:
            return "memory", schema
        if (schema := self._runner_tools.get(user_id, {}).get(tool_name)) is not None:
            return "runner", schema
        return None

    def get_schema(self, user_id: int, tool_name: str) -> dict[str, Any] | None:
        return found[1] if (found := self._lookup(user_id, tool_name)) else None

    def get_location(self, user_id: int, tool_name: str) -> ToolLocation | Literal["unknown"]:
        return found[0] if (found := self._lookup(user_id, tool_name)) else "unknown"

    async def execute_backend_tool(self, name: str, args: dict[str, Any], **context: Any) -> str | DelegateAction:
        """``args`` 须已剔除 RESERVED_KEYS；注入的 ``context`` 覆盖同名参数。"""
        tool = self._backend_tools.get(name)
        if tool is None:
            return tool_error(f"Tool {name} not found in backend registry.")
        try:
            result = tool.func(**{**args, **context})
            if inspect.isawaitable(result):
                result = await result
        except SQLAlchemyError:
            # 语句与参数只进服务端日志，不作为工具结果交给模型
            logger.exception("Database error executing backend tool", extra={"tool_name": name})
            return tool_error(f"Tool {name} failed because of a temporary storage error.")
        except OSError:
            logger.exception("Operating system error executing backend tool", extra={"tool_name": name})
            return tool_error(
                f"Tool {name} did not complete because of an operating system or connection error. Check any work already started before repeating it.",
            )
        except Exception as e:
            logger.exception("Error executing backend tool", extra={"tool_name": name})
            return tool_error(redact_sensitive_text(str(e)))

        # DelegateAction 是控制动作不是结果，原样交回调用方（对话执行层）接管
        return result if isinstance(result, (str, DelegateAction)) else json.dumps(result, ensure_ascii=False)


REGISTRY = ToolsRegistry()
