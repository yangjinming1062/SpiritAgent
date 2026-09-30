import asyncio
import importlib
import inspect
import json
import logging
import pkgutil
import threading
import time
from collections.abc import Callable
from typing import Any

from utils import redact_sensitive_text

import tools

from .toolsets import excluded_tool_names, get_disabled_toolset_ids

logger = logging.getLogger(__name__)

# 结果大小唯一真源，公共读取入口为 get_max_result_size。
DEFAULT_MAX_RESULT_SIZE_CHARS: int = 100_000


def tool_error(msg: str, **extra) -> str:
    """构造一个 JSON 错误信封。"""
    return json.dumps({"error": str(msg)} | extra, ensure_ascii=False)


class ToolError(Exception):
    """工具无法执行；dispatch 转 JSON 信封，async_dispatch 上抛由调用方映射错误帧。"""


class ToolRegistry:
    """把工具名路由到处理函数的单例注册中心。"""

    def __init__(self) -> None:
        self._tools: dict[str, Callable] = {}
        self._schemas: dict[str, dict | Callable[[], dict]] = {}
        self._check_fns: dict[str, Callable[[], bool]] = {}
        self._check_fn_cache: dict[str, tuple[bool, float, float]] = {}
        self._check_fn_ttl_seconds: float = 30.0
        self._check_fn_suppression_seconds: float = 60.0
        # 签名探测缓存：tool name -> 是否接受 cancel_token=；进程内不变，探测一次即可。
        self._supports_cancel_token: dict[str, bool] = {}
        self._import_failures: dict[str, str] = {}
        self._lock = threading.RLock()

    def record_import_failure(self, name: str, error: str) -> None:
        with self._lock:
            self._import_failures[name] = error

    def get_import_failures(self) -> dict[str, str]:
        with self._lock:
            return dict(self._import_failures)

    def register_tool(
        self,
        name: str,
        *,
        schema: dict | Callable[[], dict],
        check_fn: Callable[[], bool] | None = None,
    ) -> Callable[[Callable], Callable]:
        """注册工具；依赖配置的说明用无参工厂，每次 get_schemas 按当前配置生成。"""

        def decorator(func: Callable) -> Callable:
            with self._lock:
                self._tools[name] = func
                self._schemas[name] = schema
                if check_fn is not None:
                    self._check_fns[name] = check_fn
            return func

        return decorator

    def is_tool_available(self, name: str) -> bool:
        """能力探测：TTL 缓存 30s，成功后 60s 内的瞬时失败保留上次可用判定（见 README）。"""
        with self._lock:
            check = self._check_fns.get(name)
            if check is None:
                return name in self._tools
            cached = self._check_fn_cache.get(name)

        now = time.monotonic()
        if cached is not None:
            last_ok, probed_at, suppress_until = cached
            if now - probed_at < self._check_fn_ttl_seconds:
                return last_ok
            if last_ok and now < suppress_until:
                return True

        try:
            ok = bool(check())
        except Exception:
            ok = False

        now = time.monotonic()
        with self._lock:
            prior = self._check_fn_cache.get(name)
            suppress_until = now + self._check_fn_suppression_seconds if ok else (prior[2] if prior else now)
            self._check_fn_cache[name] = (ok, now, suppress_until)
        return ok

    def get_all_tool_names(self) -> list[str]:
        """返回已注册工具名的快照(用于 ``get_schemas_for_llm`` 等过滤流程)。"""
        with self._lock:
            return list(self._tools.keys())

    def get_schemas_for_llm(self, disabled_toolset_ids: set[str]) -> list[dict]:
        """按 ``toolsets.disabled`` 过滤后的 schema；一次性持锁取快照。"""
        with self._lock:
            items = list(self._schemas.items())

        excluded = excluded_tool_names(disabled_toolset_ids, {n for n, _ in items})
        schemas: list[dict] = []
        for name, schema in items:
            if name in excluded or not self.is_tool_available(name):
                continue
            if isinstance(schema, dict):
                schemas.append(schema)
                continue
            try:
                schemas.append(schema())
            except Exception:
                logger.exception("Could not build schema for tool %s; omitting it from the tool list", name)
        return schemas

    def get_max_result_size(self) -> int:
        """返回工具结果的大小上限。"""
        return DEFAULT_MAX_RESULT_SIZE_CHARS

    def dispatch(self, name: str, args: dict, **kwargs: Any) -> str:
        """沙箱 RPC 同步入口，返回 JSON；同受 toolsets.disabled 约束，不可在事件循环内调用。"""
        with self._lock:
            func = self._tools.get(name)
        if not func:
            logger.error(f"Tool {name} not found locally.")
            return json.dumps({"error": f"Tool '{name}' not found locally."})
        if name in excluded_tool_names(get_disabled_toolset_ids(), {name}):
            return json.dumps({"error": f"Tool '{name}' is disabled in the user's tool settings"})

        try:
            if inspect.iscoroutinefunction(func):
                try:
                    asyncio.get_running_loop()
                except RuntimeError:
                    result = asyncio.run(func(args, **kwargs))
                else:
                    return json.dumps(
                        {
                            "error": "Cannot run async tool inside an existing event loop. Refactor to sync or use run_coroutine_threadsafe.",
                        },
                    )
            else:
                result = func(args, **kwargs)
        except Exception as e:
            logger.error(f"Error executing {name}: {e}")
            return json.dumps({"error": redact_sensitive_text(f"Tool execution failed: {type(e).__name__}: {e}")})

        return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)

    async def async_dispatch(
        self,
        name: str,
        args: dict,
        cancel_token: threading.Event | None = None,
        **kwargs: Any,
    ) -> Any:
        """异步入口，供 WebSocket 服务器调用；抛出 ``ToolError`` 让调用方映射 JSON-RPC 错误帧。"""
        with self._lock:
            func = self._tools.get(name)
        if not func:
            raise ToolError(f"Tool '{name}' not found locally.")

        if self._signature_supports_token(name, func):
            kwargs = {**kwargs, "cancel_token": cancel_token}

        try:
            if inspect.iscoroutinefunction(func):
                raw = await func(args, **kwargs)
            else:
                raw = await asyncio.to_thread(func, args, **kwargs)
        except ToolError:
            raise
        except Exception as e:
            logger.error(f"Error executing {name}: {e}")
            raise ToolError(redact_sensitive_text(f"Tool execution failed: {type(e).__name__}: {e}")) from e

        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return raw
        return raw

    def _signature_supports_token(self, name: str, func: Callable) -> bool:
        """探测工具函数是否接受 ``cancel_token`` 关键字参数；探测结果进程内缓存。"""
        with self._lock:
            cached = self._supports_cancel_token.get(name)
        if cached is not None:
            return cached
        try:
            params = inspect.signature(func).parameters
        except (TypeError, ValueError):
            result = False
        else:
            result = "cancel_token" in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
        with self._lock:
            self._supports_cancel_token[name] = result
        return result


registry = ToolRegistry()


def discover_builtin_tools() -> dict[str, str]:
    """导入 ``tools`` 下全部模块触发注册；任何导入失败都记为失败，不视为可选。"""
    # 包导入失败已在循环体内记录；onerror 防止 walk_packages 随后重试导入时抛出并中断其余模块的发现。
    for info in pkgutil.walk_packages(tools.__path__, tools.__name__ + ".", onerror=lambda _name: None):
        if info.name == __name__:
            continue
        try:
            importlib.import_module(info.name)
        except Exception as exc:
            logger.error("Could not import tool module %s: %s", info.name, exc, exc_info=True)
            registry.record_import_failure(info.name, f"{type(exc).__name__}: {exc}")
    return registry.get_import_failures()
