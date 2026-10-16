"""工具目录、可用性缓存和统一执行边界。"""

import asyncio
import threading
import time
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager
from typing import Any

from spirit_agent_runner.contracts.context import invocation_scope
from spirit_agent_runner.contracts.tooling import ToolContext, ToolDefinition, ToolError
from spirit_agent_runner.infrastructure.async_bridge import run_in_thread
from spirit_agent_runner.infrastructure.redact import redact_sensitive_text


class AvailabilityCache:
    def __init__(self) -> None:
        self._values: dict[tuple[Callable[[], bool], int], tuple[bool, float]] = {}
        self._generation = 0
        self._lock = threading.Lock()

    def invalidate(self) -> None:
        with self._lock:
            self._generation += 1
            self._values.clear()

    def available(self, check: Callable[[], bool] | None, config_generation: int) -> bool:
        if check is None:
            return True
        key = (check, config_generation)
        with self._lock:
            generation = self._generation
            cached = self._values.get(key)
        now = time.monotonic()
        if cached is not None and now - cached[1] < (60.0 if cached[0] else 30.0):
            return cached[0]
        try:
            result = bool(check())
        except Exception:
            result = False
        with self._lock:
            if self._generation == generation:
                self._values[key] = result, time.monotonic()
        return result


class ToolCatalog:
    def __init__(self, definitions: Iterable[ToolDefinition]) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        for definition in definitions:
            if definition.name in self._tools:
                raise ValueError(f"Duplicate tool definition: {definition.name}")
            schema = definition.schema
            if isinstance(schema, dict) and schema.get("name") != definition.name:
                raise ValueError(f"Tool schema name mismatch: {definition.name}")
            self._tools[definition.name] = definition
        self.availability = AvailabilityCache()

    @property
    def names(self) -> list[str]:
        return list(self._tools)

    def definition(self, name: str, context: ToolContext) -> ToolDefinition:
        definition = self._tools.get(name)
        if definition is None:
            raise ToolError(f"Tool '{name}' not found locally.")
        disabled = context.config.get("toolsets", {})
        disabled = disabled.get("disabled", []) if isinstance(disabled, dict) else []
        disabled_ids = (
            {item.strip() for item in disabled if isinstance(item, str)} if isinstance(disabled, list) else set()
        )
        if definition.toolset_id in disabled_ids:
            raise ToolError(f"Tool '{name}' is disabled in the user's tool settings")
        return definition

    def schemas(self, context: ToolContext) -> list[dict[str, Any]]:
        schemas = []
        for name in self._tools:
            try:
                definition = self.definition(name, context)
            except ToolError:
                continue
            if self.availability.available(definition.check_fn, context.config_generation):
                schema = definition.schema
                schemas.append(schema() if callable(schema) else schema)
        return schemas

    def validate(self) -> None:
        for definition in self._tools.values():
            schema = definition.schema() if callable(definition.schema) else definition.schema
            if schema.get("name") != definition.name or not isinstance(schema.get("parameters"), dict):
                raise ValueError(f"Invalid schema: {definition.name}")


class ToolExecutor:
    def __init__(self, catalog: ToolCatalog, scope: Callable[[], AbstractContextManager[Any]]) -> None:
        self.catalog = catalog
        self._scope = scope

    def schemas(self, context: ToolContext) -> list[dict[str, Any]]:
        with invocation_scope(context), self._scope():
            return self.catalog.schemas(context)

    async def invoke(self, context: ToolContext, name: str, args: dict[str, Any]) -> Any:
        definition = self.catalog.definition(name, context)
        if context.cancel_event.is_set():
            raise asyncio.CancelledError
        try:
            with invocation_scope(context), self._scope():
                if definition.is_async:
                    raw = await definition.handler(context, args)
                else:
                    raw = await run_in_thread(definition.handler, context, args, on_cancel=context.cancel_event.set)
            return raw
        except asyncio.CancelledError:
            context.cancel_event.set()
            raise
        except ToolError as error:
            raise ToolError(redact_sensitive_text(str(error), config=context.config)) from error
        except Exception as error:
            raise ToolError(
                redact_sensitive_text(f"Tool execution failed: {type(error).__name__}: {error}", config=context.config),
            ) from error

    def invoke_sync(self, context: ToolContext, name: str, args: dict[str, Any]) -> Any:
        """代码子进程的工作线程入口，与直接调用共享策略和上下文。"""
        definition = self.catalog.definition(name, context)
        if context.cancel_event.is_set():
            raise ToolError("cancelled")
        try:
            with invocation_scope(context), self._scope():
                if definition.is_async:
                    return asyncio.run(definition.handler(context, args))
                return definition.handler(context, args)
        except ToolError as error:
            raise ToolError(redact_sensitive_text(str(error), config=context.config)) from error
        except Exception as error:
            raise ToolError(
                redact_sensitive_text(f"Tool execution failed: {type(error).__name__}: {error}", config=context.config),
            ) from error
