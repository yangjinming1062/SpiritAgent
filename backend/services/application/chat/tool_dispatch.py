import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from components import async_trace_span, redact_sensitive_text, safe_json_loads, tool_error

from services.contracts import DelegateAction, MediaTurnState, MemoryScope, SceneTurnState
from services.infrastructure.desktop import MANAGER, dispatch_device_call
from services.infrastructure.llm import UserLlmConfig
from services.infrastructure.tool_runtime import (
    REGISTRY,
    RESERVED_KEYS,
    ToolCallGuardrailController,
    check_file_safety,
    coerce_tool_args,
    file_mutation_result_landed,
    is_multimodal_tool_result,
    make_tool_result_message,
    should_parallelize_tool_batch,
)

from .chat_emitter import Emitter
from .message_sanitization import parse_tool_call_arguments
from .native_memory import NativeMemory

# 委派执行器由 orchestrator 注入（run_delegated_turn 绑定回合入口），避免模块级循环导入。
DelegateExecutor = Callable[[DelegateAction, int, UserLlmConfig], Awaitable[str]]


@dataclass(frozen=True)
class _ToolDispatchContext:
    """贯穿单轮工具派发的上下文参数包。"""

    user_id: int
    llm_config: UserLlmConfig
    user_settings: dict[str, Any]
    session_id: str
    memory_scope: MemoryScope | None
    native_memory: NativeMemory | None
    guardrails: ToolCallGuardrailController
    emitter: Emitter
    delegate_executor: DelegateExecutor
    media_turn: MediaTurnState
    headless: bool
    excluded_tool_names: frozenset[str]
    scene_turn: SceneTurnState


def matched_tool_names(output: object) -> list[str]:
    """``search_tools`` 结果里解锁的工具名；历史续读与本轮派发共用。"""
    parsed = safe_json_loads(output) if isinstance(output, str) else output
    if not isinstance(parsed, dict) or not isinstance(parsed.get("matched_tools"), list):
        return []
    return [str(tool["name"]) for tool in parsed["matched_tools"] if isinstance(tool, dict) and tool.get("name")]


def _redact_tool_payload(result_str: str) -> str | list:
    """对结果脱敏；解包 ``_multimodal`` 包裹后对每个 text 段逐段脱敏。"""
    if result_str.lstrip().startswith("{"):
        parsed = safe_json_loads(result_str)
        if is_multimodal_tool_result(parsed):
            for p in parsed["content"]:
                if isinstance(p, dict) and p.get("type") == "input_text" and "text" in p:
                    p["text"] = redact_sensitive_text(p["text"])
            return parsed["content"]
    return redact_sensitive_text(result_str)


async def _dispatch_runner_tool(
    user_id: int,
    name: str,
    args: dict,
    call_id: str,
    session_id: str,
    *,
    headless: bool = False,
    memory_scope: MemoryScope | None,
) -> str:
    """把 runner 工具调用作为用户级设备指令推给桌面 WS，并等待其结果。

    不经回合 emitter：设备派发不是聊天帧，它靠 call_id 关联、与用户在看哪个会话无关。载荷里的
    session_id 只描述来源，不参与路由；headless 要求客户端照常执行但不展示工作态或工具流。
    """
    skill_scope = None
    if name in {"skills_list", "skill_view", "skill_manage"}:
        if memory_scope is None:
            return tool_error("Learning skills are unavailable in automation")
        skill_scope = {"user_id": memory_scope.user_id, "system_preset_id": memory_scope.system_preset_id}
    # 客户端离线（未连接也无 grace session）或 Runner 未同步工具时快速失败；否则 IPC future 会挂 ipc_future_timeout_seconds（默认 300s）才返回合成超时错误。
    if not MANAGER.is_available(user_id):
        return tool_error("Desktop is offline. Tool calls require an active desktop connection.")
    if not REGISTRY.has_runner_tools(user_id):
        return tool_error("Runner is not available. No runner tools registered for this session.")
    result = await dispatch_device_call(
        user_id,
        call_id,
        {
            "name": name,
            "args": args,
            "call_id": call_id,
            "session_id": session_id,
            "headless": headless,
            "skill_scope": skill_scope,
        },
    )
    if result is None:
        return tool_error("Desktop is offline. Tool calls require an active desktop connection.")
    return result


async def _execute_single_tool(tc: dict, ctx: _ToolDispatchContext) -> dict:
    name = tc["name"]

    await ctx.emitter.send_json({"type": "tool_start", "name": name, "call_id": tc["call_id"]})

    try:
        if name in ctx.excluded_tool_names:
            return make_tool_result_message(
                name,
                tool_error(f"Tool is unavailable in this execution mode: {name}"),
                tc["call_id"],
            )
        args = coerce_tool_args(
            name,
            parse_tool_call_arguments(tc["arguments"], name),
            REGISTRY.get_schema(ctx.user_id, name),
        )
        # 在入口处统一剥离保留键，使 backend / memory / runner 三类工具都受同一过滤。
        args = {k: v for k, v in args.items() if k not in RESERVED_KEYS}

        if (blocked := check_file_safety(name, args)) is not None:
            return make_tool_result_message(name, blocked, tc["call_id"])

        tool_location = REGISTRY.get_location(ctx.user_id, name)
        async with async_trace_span(
            f"tool.{name}",
            attributes={"tool.location": tool_location, "tool.call_id": tc["call_id"]},
        ):
            match tool_location:
                case "backend":
                    # 委派工具只返回控制动作；子回合由对话执行层接管，避免工具处理器反向重入对话入口。
                    result = await REGISTRY.execute_backend_tool(
                        name,
                        args,
                        user_id=ctx.user_id,
                        llm_config=ctx.llm_config,
                        user_settings=ctx.user_settings,
                        parent_session_id=ctx.session_id,
                        excluded_tool_names=ctx.excluded_tool_names,
                        scene_turn=ctx.scene_turn,
                        media_turn=ctx.media_turn,
                    )
                    result_str = (
                        await ctx.delegate_executor(result, ctx.user_id, ctx.llm_config)
                        if isinstance(result, DelegateAction)
                        else result
                    )
                case "memory":
                    result_str = (
                        await ctx.native_memory.execute_tool(name, args)
                        if ctx.native_memory
                        else tool_error("Memory is unavailable")
                    )
                case "runner":
                    result_str = await _dispatch_runner_tool(
                        ctx.user_id,
                        name,
                        args,
                        tc["call_id"],
                        ctx.session_id,
                        headless=ctx.headless,
                        memory_scope=ctx.memory_scope,
                    )
                case _:
                    result_str = tool_error(f"Unknown tool location for {name}")

        result_str = ctx.guardrails.after_call(name, args, result_str)

        if file_mutation_result_landed(name, result_str):
            result_str += "\n[System: The file write/patch operation successfully landed.]"

        final_content = _redact_tool_payload(result_str)
        return make_tool_result_message(name, final_content, tc["call_id"])
    finally:
        await ctx.emitter.send_json({"type": "tool_end", "name": name, "call_id": tc["call_id"]})


async def _run_tool_batch(tool_calls_list: list[dict], ctx: _ToolDispatchContext) -> list[dict]:
    coros = [_execute_single_tool(tc, ctx) for tc in tool_calls_list]
    if len(tool_calls_list) > 1 and should_parallelize_tool_batch(
        [(tc["name"], tc["arguments"]) for tc in tool_calls_list],
    ):
        # ``return_exceptions=True``：单个工具抛出（如 IPC future 超时、工具 httpx 流漏出的 ``CancelledError`` 或工具体异常）不会取消兄弟协程；否则一个失败会拖住其余所有进行中的调用，挂满 ``ipc_future_timeout_seconds``（300s）才返回，本轮其他工具结果会丢失。
        results = await asyncio.gather(*coros, return_exceptions=True)
        out: list[dict] = []
        for tc, r in zip(tool_calls_list, results):
            if isinstance(r, BaseException):
                out.append(_crash_result(tc, r))
            else:
                out.append(r)
        return out
    out = []
    for tc, coro in zip(tool_calls_list, coros):
        try:
            out.append(await coro)
        except asyncio.CancelledError:
            raise
        except Exception as r:
            out.append(_crash_result(tc, r))
    return out


def _crash_result(tc: dict, exc: BaseException) -> dict:
    """为崩溃工具合成 tool_result：assistant 行已带 tool_calls 落库，缺对应结果行会让下一轮上下文出现孤立 function_call 而被供应商整体拒绝。"""
    return make_tool_result_message(tc.get("name", "<unknown>"), tool_error(f"Tool crashed: {exc!r}"), tc["call_id"])
