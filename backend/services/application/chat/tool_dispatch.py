import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from components import get_logger, redact_sensitive_text, safe_json_loads, tool_error
from modules.channels import ChannelTurnSource

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

logger = get_logger(__name__)

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
    proactive_turn: bool
    user_message: str
    authorization_check: Callable[[], Awaitable[bool]] | None = None
    channel_source: ChannelTurnSource | None = None


@dataclass
class _BatchProgress:
    """单批工具调用的进展；回合被中断时据此还原每个调用的真实状态，而不是一律记为取消。"""

    settled: dict[str, dict] = field(default_factory=dict)  # call_id → 已产生的 tool 结果消息
    started: set[str] = field(default_factory=set)


# 结果未知：回合中断时仍在运行的调用，以及历史里缺结果行的调用。
INTERRUPTED_RUNNING_ERROR = (
    "The turn was interrupted while this tool was running, so the outcome is unknown: it may have completed, "
    "partly run, or not run at all. Do not assume it failed or repeat it automatically; check its effects or ask "
    "the user first."
)
_INTERRUPTED_PENDING_ERROR = "Not executed: the turn was interrupted before this tool call started."
_INVALID_ARGUMENTS_ERROR = "Not executed: the tool arguments were not a valid JSON object. Resend the call with complete, valid JSON arguments."


def interrupted_tool_results(tool_calls_list: list[dict], progress: _BatchProgress) -> list[tuple[str, Any]]:
    """回合被中断时各工具调用应记录的结果：已产生的照实保留，运行中的记为结果未知，未开始的记为未执行。"""
    results: list[tuple[str, Any]] = []
    for tc in tool_calls_list:
        call_id = tc.get("call_id", "")
        if (settled := progress.settled.get(call_id)) is not None:
            content: Any = settled.get("content", "")
        elif call_id in progress.started:
            content = tool_error(INTERRUPTED_RUNNING_ERROR)
        else:
            content = tool_error(_INTERRUPTED_PENDING_ERROR)
        results.append((call_id, content))
    return results


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
    """把 runner 工具调用作为用户级设备指令推给桌面 WS 并等待结果。不经回合 emitter（设备派发不是聊天帧，靠 call_id 关联，与当前会话无关）；载荷 session_id 只描述来源不参与路由；headless 要求客户端照常执行但不展示工作态。"""
    skill_scope = None
    if name in {"skills_list", "skill_view", "skill_manage"}:
        if memory_scope is None:
            return tool_error("Learning skills are unavailable in automation")
        skill_scope = {"user_id": memory_scope.user_id, "system_preset_id": memory_scope.system_preset_id}
    # 客户端离线（未连接也无 grace session）或 Runner 未同步工具时快速失败；否则 IPC future 会挂满 ipc_future_timeout_seconds 才返回合成超时错误。
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


async def _execute_single_tool(tc: dict, ctx: _ToolDispatchContext, progress: _BatchProgress) -> dict:
    name = tc["name"]

    if ctx.authorization_check is not None and not await ctx.authorization_check():
        raise asyncio.CancelledError("The channel authorization was revoked")

    await ctx.emitter.send_json({"type": "tool_start", "name": name, "call_id": tc["call_id"]})

    try:
        if name in ctx.excluded_tool_names:
            return make_tool_result_message(
                name,
                tool_error(f"Tool is unavailable in this execution mode: {name}"),
                tc["call_id"],
            )
        parsed_args = parse_tool_call_arguments(tc["arguments"], name)
        if parsed_args is None:
            # 参数无法解析时不派发：以失败结果告知模型，并计入守卫，重复失败能得到换策略提示。
            result_str = tool_error(_INVALID_ARGUMENTS_ERROR)
            return make_tool_result_message(
                name,
                _redact_tool_payload(result_str),
                tc["call_id"],
                trusted_suffix=ctx.guardrails.record_call(name, {}, result_str),
            )
        args = coerce_tool_args(name, parsed_args, REGISTRY.get_schema(ctx.user_id, name))
        # 在入口处统一剥离保留键，使 backend / memory / runner 三类工具都受同一过滤。
        args = {k: v for k, v in args.items() if k not in RESERVED_KEYS}

        if (blocked := check_file_safety(name, args)) is not None:
            return make_tool_result_message(name, blocked, tc["call_id"])

        tool_location = REGISTRY.get_location(ctx.user_id, name)
        if ctx.authorization_check is not None and not await ctx.authorization_check():
            raise asyncio.CancelledError("The channel authorization was revoked")
        progress.started.add(tc["call_id"])
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
                    memory_scope=ctx.memory_scope,
                    tool_call_id=tc["call_id"],
                    proactive_turn=ctx.proactive_turn,
                    user_message=ctx.user_message,
                    channel_source=ctx.channel_source,
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

        trusted_suffix = ctx.guardrails.record_call(name, args, result_str)

        if file_mutation_result_landed(name, result_str):
            trusted_suffix += "\n[System: The file write/patch operation successfully landed.]"

        final_content = _redact_tool_payload(result_str)
        return make_tool_result_message(name, final_content, tc["call_id"], trusted_suffix=trusted_suffix)
    finally:
        await ctx.emitter.send_json({"type": "tool_end", "name": name, "call_id": tc["call_id"]})


async def _run_tracked_tool(tc: dict, ctx: _ToolDispatchContext, progress: _BatchProgress) -> dict:
    try:
        result = await _execute_single_tool(tc, ctx, progress)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        result = _crash_result(tc, exc)
    progress.settled[tc["call_id"]] = result
    return result


async def _run_tool_batch(
    tool_calls_list: list[dict],
    ctx: _ToolDispatchContext,
    progress: _BatchProgress,
) -> list[dict]:
    if len(tool_calls_list) > 1 and should_parallelize_tool_batch(
        [(tc["name"], tc["arguments"]) for tc in tool_calls_list],
    ):
        tasks = [asyncio.create_task(_run_tracked_tool(tc, ctx, progress)) for tc in tool_calls_list]
        try:
            # 普通异常由单工具转换；撤权和取消必须停止整批，不能变成可继续执行的工具错误。
            return await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    return [await _run_tracked_tool(tc, ctx, progress) for tc in tool_calls_list]


def _crash_result(tc: dict, exc: BaseException) -> dict:
    """为崩溃工具合成 tool_result：assistant 行已带 tool_calls 落库，须记下确定的失败而不是留下缺结果的调用；异常文本同普通结果一样脱敏。"""
    name = tc.get("name", "<unknown>")
    logger.error("Tool call crashed", extra={"tool_name": name, "call_id": tc["call_id"]}, exc_info=exc)
    return make_tool_result_message(name, _redact_tool_payload(tool_error(f"Tool crashed: {exc!r}")), tc["call_id"])
