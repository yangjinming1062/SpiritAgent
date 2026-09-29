import argparse
import asyncio
import contextlib
import json
import logging
import os
import platform
import sys
import threading
import time
import uuid
from typing import Any, NamedTuple

import utils.credential_files
import utils.env_passthrough
import utils.url_safety
import websockets
from runner_version import __version__
from tools import (
    ToolError,
    discover_builtin_tools,
    get_disabled_toolset_ids,
    registry,
    reset_cache,
    reset_max_read_chars_cache,
)
from tools.toolsets import excluded_tool_names
from utils import (
    CURRENT_SKILL_SCOPE,
    DesktopEndpoint,
    SkillScope,
    call_journal,
    connect_desktop,
    disk_free_bytes,
    get_spiritagent_home,
    init_runner_job_object,
    network_reachable,
    read_endpoint,
    reset_cancel_event,
    set_cancel_event,
    set_handler,
    set_inmemory_config,
    set_main_loop,
    snapshot,
)

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("spiritagent_runner")


def _require_supported_host() -> None:
    """Runner 只分发 Windows 与 macOS；其他宿主上麦克风、系统活动、桌面操作等子系统会静默失效，直接拒绝启动。"""
    if sys.platform not in {"win32", "darwin"}:
        raise SystemExit(
            f"SpiritAgent Runner does not support the {sys.platform!r} host. Supported hosts are Windows and macOS only.",
        )


_ACTIVE_WS: Any | None = None
_PENDING_RPC: dict[str, asyncio.Future[Any]] = {}
_STARTED_AT = time.time()
_RECONNECT_COUNT = 0
_current_reconnect_streak = 0

# 运行代次：每次进程启动生成一次，重连不换（进程没重启）；
# runner_ready / capabilities 变化通知 / info 三处同源携带。
_RUN_GENERATION = uuid.uuid4().hex

# 重连退避 + 端点文件轮询间隔。无硬上限；Runner 在 Desktop 进程级拆除前无限退避。
BASE_BACKOFF_S = 2.0
MAX_BACKOFF_S = 30.0
_ENDPOINT_POLL_S = 1.0

_BG_TASKS: set[asyncio.Task] = set()


class _InflightCall(NamedTuple):
    task: asyncio.Task[Any]
    cancel_event: threading.Event
    # 带 call_id 的是模型派发的调用；Client 的窗口轮询等直调不带，省略 req_id 的取消不作用于它们。
    dispatched: bool


_INFLIGHT: dict[str, _InflightCall] = {}

# PROTOCOL「反向模型请求」 Runner 单连接守卫：累计 200 帧 / 1MB 文本 / 10MB 视觉，重连清零，防止工具失控刷爆 LLM。
MAX_LLM_REQUESTS_PER_SESSION = 200
MAX_LLM_TEXT_BYTES_PER_SESSION = 1 * 1024 * 1024  # 1 MiB
MAX_LLM_VISION_BYTES_PER_SESSION = 10 * 1024 * 1024  # 10 MiB

_llm_requests_count = 0
_llm_bytes_count = 0


def _has_vision_content(kwargs: dict[str, Any]) -> bool:
    def _is_image_part(p: Any) -> bool:
        return isinstance(p, dict) and (
            p.get("type") in ("input_image", "image_url", "image") or "image_url" in p or "image" in p
        )

    for field in ("messages", "input"):
        items = kwargs.get(field)
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    content = item.get("content")
                    if isinstance(content, list) and any(_is_image_part(p) for p in content):
                        return True
                    parts = item.get("parts")
                    if isinstance(parts, list) and any(_is_image_part(p) for p in parts):
                        return True
    return False


def reset_llm_rate_limits() -> None:
    """重置单连接反向 RPC 累计计数器（建立新连接时调用）。"""
    global _llm_requests_count, _llm_bytes_count
    _llm_requests_count = 0
    _llm_bytes_count = 0


async def _send(ws: Any, req_id: Any, **fields: Any) -> None:
    await ws.send(json.dumps({"jsonrpc": "2.0", "id": req_id, **fields}))


async def _send_notification(ws: Any, method: str, params: dict[str, Any], id: Any = None) -> None:
    body: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params}
    if id is not None:
        body["id"] = id
    await ws.send(json.dumps(body))


async def request_llm_from_desktop(kwargs: dict[str, Any]) -> str:
    """向 Client 发 ``request_llm`` 并返回模型文本（供 web_extract 等一次性补全；鉴权在 Client 侧完成，Runner 不持凭据）。

    Client 代理 Backend ``/api/llm/completion``：成功结果为 ``{"content": str, "usage": dict|null}``，失败走 JSON-RPC error。
    缺少文本字段按协议错误拒绝，不降级为空串掩盖失败。
    """
    global _llm_requests_count, _llm_bytes_count

    if (ws := _ACTIVE_WS) is None:
        raise RuntimeError("No active WebSocket connection")

    payload_bytes = len(json.dumps(kwargs, default=str).encode("utf-8"))
    max_bytes = MAX_LLM_VISION_BYTES_PER_SESSION if _has_vision_content(kwargs) else MAX_LLM_TEXT_BYTES_PER_SESSION

    if _llm_requests_count >= MAX_LLM_REQUESTS_PER_SESSION:
        raise RuntimeError("request_llm rate-limited by runner: exceeded maximum requests per session (200)")

    if _llm_bytes_count + payload_bytes > max_bytes:
        raise RuntimeError("request_llm rate-limited by runner: exceeded maximum payload bytes per session")

    # 尊重调用方传入的 per-call 超时: 下限设 1.0s 避免出现 0 秒等; 不设上限, 上限由调用方在业务预算处自行约束。
    try:
        timeout_s = float(kwargs.get("timeout", 120.0))
    except (TypeError, ValueError):
        timeout_s = 120.0
    timeout_s = max(timeout_s, 1.0)

    req_id = f"req_llm_{uuid.uuid4().hex[:8]}"
    fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
    _PENDING_RPC[req_id] = fut

    _llm_requests_count += 1
    _llm_bytes_count += payload_bytes

    try:
        await _send_notification(ws, "request_llm", kwargs, id=req_id)
        result = await asyncio.wait_for(fut, timeout=timeout_s)
    except TimeoutError as e:
        raise TimeoutError(f"request_llm: no response within {timeout_s:.0f}s") from e
    finally:
        _PENDING_RPC.pop(req_id, None)

    if not isinstance(result, dict) or not isinstance(content := result.get("content"), str):
        raise RuntimeError(f"request_llm: response has no string 'content' ({type(result).__name__})")
    return content


async def process_request(ws: Any, req: dict[str, Any]) -> None:
    req_id = req.get("id")
    method = req.get("method")
    params = req.get("params", {})

    # notification 不带 id; 提前丢弃, 否则心跳探测会刷出 -32601 log noise。
    if req_id is None:
        return

    try:
        if method == "spiritagent.cancel":
            target_req_id = params.get("req_id")
            if target_req_id is None:
                targets = [call for call in _INFLIGHT.values() if call.dispatched]
            else:
                targets = [call] if (call := _INFLIGHT.get(str(target_req_id))) else []
            if not targets:
                await _send(ws, req_id, result={"ok": False, "error": "no_in_flight"})
                return
            for call in targets:
                call.cancel_event.set()
                call.task.cancel()
            await _send(ws, req_id, result={"ok": True})
            return

        if method == "get_tools":
            await _send(ws, req_id, result={"tools": registry.get_schemas_for_llm(get_disabled_toolset_ids())})
            return

        if method == "spiritagent.info":
            await _send(ws, req_id, result=await _build_info())
            return

        if method == "spiritagent.call_result":
            call_id = params.get("call_id")
            if not isinstance(call_id, str) or not call_id:
                raise ValueError("spiritagent.call_result requires a 'call_id' string")
            record = await asyncio.to_thread(call_journal.lookup, call_id)
            await _send(
                ws,
                req_id,
                result=record if record is not None else {"call_id": call_id, "status": "not_found"},
            )
            return

        if method == "spiritagent.config.update":
            # 重置缓存, 让派生限制立刻看到新 config。
            config = params.get("config")
            if not isinstance(config, dict):
                raise ValueError("spiritagent.config.update requires a 'config' object")
            set_inmemory_config(config)
            reset_cache()
            reset_max_read_chars_cache()
            utils.env_passthrough.reset_cache()
            utils.credential_files.reset_cache()
            utils.url_safety.reset_cache()
            await _send(ws, req_id, result={"ok": True})
            return

        if method in {"execute_tool", "execute_scoped_tool"}:
            name = params.get("name")
            if not isinstance(name, str) or not name:
                raise ValueError("A non-empty 'name' string is required")
            journal_args = params.get("args", {})
            if not isinstance(journal_args, dict):
                raise ValueError("Tool 'args' must be an object")
            journal_call_id = params.get("call_id")
            if journal_call_id is not None and (not isinstance(journal_call_id, str) or not journal_call_id):
                raise ValueError("Tool 'call_id' must be a non-empty string")
            # 与 get_schemas_for_llm 同源：渲染层/直调不得绕过 toolsets.disabled。
            skill_scope = SkillScope.parse(params.get("skill_scope")) if method == "execute_scoped_tool" else None
            if name in excluded_tool_names(get_disabled_toolset_ids(), {name}):
                raise ToolError(f"Tool '{name}' is disabled in the user's tool settings")
            inflight_key = str(req_id)
            cancel_event = threading.Event()
            cur_task = asyncio.current_task()
            assert cur_task is not None
            _INFLIGHT[inflight_key] = _InflightCall(cur_task, cancel_event, dispatched=journal_call_id is not None)
            cancel_reset = set_cancel_event(cancel_event)
            scope_reset = CURRENT_SKILL_SCOPE.set(skill_scope)
            journal_claim: asyncio.Task[call_journal.ClaimOutcome] | None = None
            journal_claim_token: str | None = None
            try:
                if journal_call_id:
                    journal_claim = asyncio.create_task(
                        asyncio.to_thread(
                            call_journal.claim,
                            journal_call_id,
                            name,
                            journal_args,
                            os.getpid(),
                            skill_scope,
                        ),
                    )
                    # 取消不能停止认领线程；保留其结果，取消路径才能回收已经获得的执行权。
                    outcome = await asyncio.shield(journal_claim)
                    journal_claim_token = outcome.claim_token
                    if not outcome.should_execute:
                        if outcome.disposition == "completed":
                            await _send(ws, req_id, result=outcome.result)
                            return
                        await _send(
                            ws,
                            req_id,
                            error={
                                "code": -32011 if outcome.disposition == "unknown" else -32000,
                                "message": outcome.error
                                or f"call_id {journal_call_id!r} rejected: {outcome.disposition}",
                                "data": {"disposition": outcome.disposition},
                            },
                        )
                        return
                try:
                    result = await registry.async_dispatch(name, journal_args, cancel_token=cancel_event)
                except ToolError as e:
                    if journal_call_id and journal_claim_token is not None:
                        await asyncio.to_thread(call_journal.mark_failed, journal_call_id, journal_claim_token, str(e))
                    await _send(ws, req_id, error={"code": -32000, "message": str(e)})
                    return
                if journal_call_id and journal_claim_token is not None:
                    await asyncio.to_thread(
                        call_journal.mark_completed,
                        journal_call_id,
                        journal_claim_token,
                        result,
                    )
                await _send(ws, req_id, result=result)
                return
            except asyncio.CancelledError:
                if journal_call_id and journal_claim is not None:
                    await call_journal.settle_cancelled_claim(journal_call_id, journal_claim)
                raise
            finally:
                CURRENT_SKILL_SCOPE.reset(scope_reset)
                reset_cancel_event(cancel_reset)
                _INFLIGHT.pop(inflight_key, None)

        await _send(ws, req_id, error={"code": -32601, "message": "Method not found"})
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            await _send(ws, req_id, error={"code": -32000, "message": "cancelled"})
        raise
    except Exception as e:
        # 回复本身绝不能再抛: 中途断连的 handler 会让后台任务以未捕获异常死去。
        with contextlib.suppress(Exception):
            await _send(ws, req_id, error={"code": -32000, "message": str(e)})


def _resolve_pending_rpc(data: dict[str, Any]) -> None:
    """把 Client 对 ``request_llm`` 的响应交给等待方；等待方已超时或取消时丢弃迟到响应。"""
    req_id = data.get("id")
    fut = _PENDING_RPC.pop(req_id, None) if isinstance(req_id, str) else None
    if fut is None or fut.done():
        logger.debug("Dropping response for unknown or settled request id %r", req_id)
        return
    if "error" in data:
        err = data["error"]
        fut.set_exception(RuntimeError(err.get("message", str(err)) if isinstance(err, dict) else str(err)))
    else:
        fut.set_result(data.get("result"))


def _fail_pending_rpcs(reason: str) -> None:
    """对所有 in-flight 的 ``request_llm`` future 抛失败, 让调用方的 ``wait_for`` 迅速返回。

    用 ``set_exception`` 而不是 cancel 是为了把断连原因透给 LLM；已 done 的 future（响应已取走或 ``wait_for`` 已取消）跳过。
    """
    for fut in list(_PENDING_RPC.values()):
        if not fut.done():
            fut.set_exception(ConnectionError(reason))
    _PENDING_RPC.clear()


async def runner_loop(endpoint: DesktopEndpoint) -> None:
    """与 Desktop IPC 的长连接主循环: 维护持久连接、处理重连退避、派发 RPC、Ready 时主动通知。"""
    global _ACTIVE_WS, _RECONNECT_COUNT, _current_reconnect_streak

    current_endpoint: DesktopEndpoint | None = endpoint
    attempt = 0

    while True:
        # 等 Desktop 发布 endpoint(重启窗口、文件缺失/陈旧)是正常稳态, 不是连接失败 — 不能消耗下面的重连预算。
        tried_connect = current_endpoint is not None
        if current_endpoint is None:
            logger.info("Desktop endpoint not ready, waiting for endpoint file...")
        else:
            logger.info(f"Connecting to Desktop IPC: {current_endpoint.path} (attempt {attempt + 1})")
            try:
                connection = await connect_desktop(current_endpoint)
                async with connection as ws:
                    _ACTIVE_WS = ws
                    try:
                        reset_llm_rate_limits()
                        await _send_notification(ws, "runner_ready", await _runner_ready_payload())
                        attempt = 0
                        _current_reconnect_streak = 0
                        async for message in ws:
                            try:
                                data = json.loads(message)
                            except json.JSONDecodeError:
                                logger.error("Invalid JSON received")
                                continue
                            # JSON-RPC 帧必须是 JSON 对象；其他形态打 log 跳过，防止 ``data.get(...)`` 抛异常断开连接。
                            if not isinstance(data, dict):
                                logger.error("Non-object JSON frame received: %r", type(data).__name__)
                                continue
                            if "method" not in data:
                                _resolve_pending_rpc(data)
                                continue
                            t = asyncio.create_task(process_request(ws, data))
                            _BG_TASKS.add(t)
                            t.add_done_callback(_BG_TASKS.discard)
                    finally:
                        _ACTIVE_WS = None
                        # 排空待发 request_llm future, 让调用方 ``wait_for`` 携带断连原因迅速返回。
                        _fail_pending_rpcs("Runner WS disconnected before response arrived")

            except websockets.exceptions.ConnectionClosed:
                logger.warning("WebSocket connection closed by Desktop.")
            except websockets.exceptions.InvalidStatus as e:
                # Desktop 升级前 token 校验返回 HTTP 401: 本进程仍持有上一个会话的 token(Desktop 重启过)。
                # 丢弃缓存 endpoint, 下一次从 endpoint 文件重读新的路径+token — 重试旧的一定 401 白白烧掉重试预算。
                logger.warning(f"Desktop rejected handshake ({e.response.status_code}); refreshing endpoint")
                current_endpoint = None
            except Exception as e:
                logger.error(f"WebSocket error: {e}")

        # 从 Desktop 写的文件读取最新 endpoint(路径+token); 文件缺失/陈旧就保留缓存的 endpoint。
        if new_endpoint := read_endpoint():
            current_endpoint = new_endpoint

        if not tried_connect:
            await asyncio.sleep(_ENDPOINT_POLL_S)
            continue

        _RECONNECT_COUNT += 1
        _current_reconnect_streak += 1
        attempt += 1

        backoff = min(BASE_BACKOFF_S * (2 ** min(attempt - 1, 4)), MAX_BACKOFF_S)
        logger.info(f"Reconnecting in {backoff:.1f}s (attempt {attempt})")
        await asyncio.sleep(backoff)


async def _probe_capabilities() -> tuple[dict[str, Any], dict[str, Any], bool]:
    """返回 (能力, 健康详情, 探测是否失败)。失败时撤销全部可选能力，并以 ``probe_failed`` 区分于"全部不可用"。"""
    try:
        caps, health = await asyncio.to_thread(snapshot)
    except Exception as e:
        logger.warning(f"capabilities probe failed: {e}")
        return {}, {}, True
    return caps, health, False


async def _runner_ready_payload() -> dict[str, Any]:
    """``runner_ready`` 负载：Desktop 据此决定是否暴露依赖可选系统能力的功能；``capabilities`` 中缺失的键视为不可用。"""
    caps, health, probe_failed = await _probe_capabilities()
    return {
        "version": __version__,
        "run_generation": _RUN_GENERATION,
        "pid": os.getpid(),
        "capabilities": caps,
        "capabilities_health": health,
        "probe_failed": probe_failed,
        "reconnect_streak": _current_reconnect_streak,
    }


async def _watch_capabilities(interval_s: float = 120.0) -> None:
    """周期重探测能力，快照或探测状态变化时向客户端发 ``runner_capabilities_changed``。

    探测失败发 ``probe_failed=True`` 的降级通知，撤销可选能力而不是让客户端保留陈旧的可用认知。
    """
    last: tuple[dict[str, Any], bool] | None = None
    while True:
        await asyncio.sleep(interval_s)
        if (ws := _ACTIVE_WS) is None:
            continue
        caps, _, probe_failed = await _probe_capabilities()
        if (caps, probe_failed) == last:
            continue
        try:
            await _send_notification(
                ws,
                "runner_capabilities_changed",
                {
                    "run_generation": _RUN_GENERATION,
                    "capabilities": caps,
                    "probe_failed": probe_failed,
                },
            )
        except Exception as e:
            # 发送失败最常见于断连瞬间；下次循环 _ACTIVE_WS 已更新，无需特殊处理。
            logger.debug(f"capabilities_changed notification failed: {e}")
            continue
        last = (caps, probe_failed)


async def _build_info() -> dict[str, Any]:
    """``spiritagent.info`` 的运行快照：能力之外含进程与系统状态，供诊断区分陈旧进程与冷启动。"""
    caps, health, probe_failed = await _probe_capabilities()
    # ``network_reachable`` 可能阻塞 ~1.5s; 放到线程里避免探测期间心跳 / spiritagent.cancel 失去响应。
    reachable = await asyncio.to_thread(network_reachable)
    return {
        "version": __version__,
        "run_generation": _RUN_GENERATION,
        "pid": os.getpid(),
        "started_at": _STARTED_AT,
        "uptime_seconds": round(time.time() - _STARTED_AT, 2),
        "reconnect_count": _RECONNECT_COUNT,
        "capabilities": caps,
        "capabilities_health": health,
        "probe_failed": probe_failed,
        "system": {
            "platform": sys.platform,
            "python": sys.version.split()[0],
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "tool_count": len(registry.get_all_tool_names()),
        "failed_tools": registry.get_import_failures(),
        "network_reachable": reachable,
        "disk_free_bytes": disk_free_bytes(get_spiritagent_home()),
    }


async def _runner_main(endpoint: DesktopEndpoint) -> None:
    set_main_loop(asyncio.get_running_loop())
    # 启动核对：上次运行中断的认领（持有进程已死且无终态）标 unknown 待核对，过期终态清理。
    stale = await asyncio.to_thread(call_journal.sweep_stale_claims, os.getpid())
    if stale:
        logger.info("call journal: %d interrupted claim(s) marked unknown", stale)
    watcher = asyncio.create_task(_watch_capabilities())
    _BG_TASKS.add(watcher)
    watcher.add_done_callback(_BG_TASKS.discard)
    try:
        await runner_loop(endpoint)
    finally:
        watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher


def main() -> None:
    _require_supported_host()
    init_runner_job_object()
    parser = argparse.ArgumentParser(description="SpiritAgent Runner Server")
    parser.add_argument(
        "--desktop-endpoint",
        required=True,
        help="Desktop IPC path (Windows named pipe or Unix socket path)",
    )
    args = parser.parse_args()

    # token 只经环境变量下发，避免出现在同机进程列表的 argv 中。
    if not (token := os.environ.get("SPIRITAGENT_DESKTOP_TOKEN")):
        parser.error("Desktop handshake token is required (SPIRITAGENT_DESKTOP_TOKEN)")

    if import_failures := discover_builtin_tools():
        logger.error("Some builtin tool modules failed to load: %s", import_failures)
    set_handler(request_llm_from_desktop)

    try:
        asyncio.run(_runner_main(DesktopEndpoint(path=args.desktop_endpoint, token=token)))
    except KeyboardInterrupt:
        logger.info("Runner stopped.")


if __name__ == "__main__":
    main()
