import argparse
import asyncio
import contextlib
import json
import logging
import os
import platform
import sys
import time
import uuid
from typing import Any

import utils.credential_files
import utils.env_passthrough
import websockets
from runner_version import __version__
from tools import (
    ToolError,
    discover_builtin_tools_strict,
    get_disabled_toolset_ids,
    registry,
    reset_cache,
    reset_max_read_chars_cache,
)
from tools.browser import reset_session_caches
from tools.toolsets import excluded_tool_names
from utils import (
    CURRENT_SKILL_SCOPE,
    CancellationToken,
    DesktopEndpoint,
    SkillScope,
    connect_desktop,
    disk_free_bytes,
    get_spiritagent_home,
    init_runner_job_object,
    network_reachable,
    read_endpoint,
    reset_current_request,
    set_current_request,
    set_handler,
    set_inmemory_config,
    set_interrupt,
    set_local_interrupt,
    set_main_loop,
    snapshot,
    snapshot_health,
)

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("spiritagent_runner")


def _require_supported_host() -> None:
    """Runner 只在 Windows + macOS 上分发; 在其他平台直接拒绝启动 — 跑在不支持的主机上, 多项子系统(麦克风探测、system-activity 后端、cu_* 工具)都会悄悄失效, 不如直接在此硬失败, 给运维/CI 一个明确信号。"""
    if sys.platform not in {"win32", "darwin"}:
        raise SystemExit(
            f"SpiritAgent Runner does not support the {sys.platform!r} host. Supported hosts are Windows and macOS only.",
        )


_ACTIVE_WS: Any | None = None
_RUNNER_LOOP: asyncio.AbstractEventLoop | None = None
_PENDING_RPC: dict[str, asyncio.Future] = {}
_STARTED_AT = time.time()
_RECONNECT_COUNT = 0
_current_reconnect_streak = 0

# 重连退避 + 端点文件轮询间隔。H6: 不再有硬上限; Runner 在 Desktop 进程级拆除前无限退避。
BASE_BACKOFF_S = 2.0
MAX_BACKOFF_S = 30.0
_ENDPOINT_POLL_S = 1.0

_BG_TASKS: set[asyncio.Task] = set()
_INFLIGHT_BY_REQ_ID: dict[str, asyncio.Task] = {}
_CURRENT_EXECUTE_TASK: asyncio.Task | None = None
_CURRENT_EXECUTE_REQ_ID: str | None = None
_ACTIVE_CANCELLATIONS: dict[str, CancellationToken] = {}

# PROTOCOL §3 反向 RPC 速率守卫：单会话累计限额（200 帧 / 1MB 文本 / 10MB 视觉），防止工具失控刷爆 LLM。
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
    """重置单会话反向 RPC 累计计数器（在建立新连接或测试时调用）。"""
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
    """向 Desktop 发 ``request_llm`` 并返回原始 LLM 文本响应(供 vision / web_extract 等一次性补全的工具使用; 所有网络鉴权都在 Desktop 侧完成, runner 自身不带凭据)。

    Backend 返回 ``{ "content": str, "usage": dict|null }`` 或原始 OpenAI 兼容体; 这里抽取出文本
    给调用方纯 ``str``。不携带文本字段的 dict 降级为 ``""``(见 ``_extract_llm_content``); 既不是 str 也不是
    dict 的载荷按协议错误拒绝。
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

    req_id = f"req_llm_{uuid.uuid4().hex[:8]}"
    fut: asyncio.Future = asyncio.Future()
    _PENDING_RPC[req_id] = fut

    _llm_requests_count += 1
    _llm_bytes_count += payload_bytes

    await _send_notification(ws, "request_llm", kwargs, id=req_id)

    # 尊重调用方传入的 per-call 超时: 下限设 1.0s 避免出现 0 秒等; 不设上限, 上限由调用方在业务预算处自行约束。
    try:
        timeout_s = float(kwargs.get("timeout", 120.0))
    except (TypeError, ValueError):
        timeout_s = 120.0
    timeout_s = max(timeout_s, 1.0)

    try:
        result = await asyncio.wait_for(fut, timeout=timeout_s)
    finally:
        _PENDING_RPC.pop(req_id, None)

    if isinstance(result, dict):
        if "error" in result and isinstance(result["error"], dict):
            reason = result["error"].get("reason", "unknown")
            raise RuntimeError(
                f"request_llm: backend error ({reason}): {result['error'].get('message', result['error'])}",
            )
        return _extract_llm_content(result)
    if isinstance(result, str):
        return result
    raise RuntimeError(f"request_llm: backend returned {type(result).__name__}, expected str or dict with content")


def _extract_llm_content(result: dict[str, Any]) -> str:
    """``/api/llm/completion`` 代理返回 ``{"content": str, "usage": ...}``。缺失时返回空串，让调用方优雅处理而非让整个工具调用失败。"""
    content = result.get("content")
    return content if isinstance(content, str) else ""


async def process_request(ws: Any, req: dict[str, Any]) -> None:
    global _CURRENT_EXECUTE_TASK, _CURRENT_EXECUTE_REQ_ID
    req_id = req.get("id")
    method = req.get("method")
    params = req.get("params", {})

    # notification 不带 id; 提前丢弃, 否则心跳探测会刷出 -32601 log noise。
    if req_id is None and method:
        return

    # 新请求到达时清理上一条请求残留的 per-thread interrupt, 防止当前请求的工具立刻被 bail。
    set_interrupt(False, thread_id=None)
    try:
        if method == "spiritagent.cancel":
            target_req_id = params.get("req_id")
            target_task: asyncio.Task | None = None
            target_local: str | None = None
            if target_req_id is not None:
                target_task = _INFLIGHT_BY_REQ_ID.get(target_req_id)
                target_local = target_req_id
            elif _CURRENT_EXECUTE_TASK is not None and not _CURRENT_EXECUTE_TASK.done():
                target_task = _CURRENT_EXECUTE_TASK
                target_local = _CURRENT_EXECUTE_REQ_ID
            if target_task is None:
                await _send(ws, req_id, result={"ok": False, "error": "no_in_flight"})
                return
            target_task.cancel()
            if target_local is not None:
                set_local_interrupt(target_local, True)
                token = _ACTIVE_CANCELLATIONS.get(target_local)
                if token is not None:
                    token.set()
            await _send(ws, req_id, result={"ok": True})
            return

        if method == "get_tools":
            await _send(ws, req_id, result={"tools": registry.get_schemas_for_llm(get_disabled_toolset_ids())})
            return

        if method == "spiritagent.info":
            await _send(ws, req_id, result=await _build_info())
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
            reset_session_caches()
            await _send(ws, req_id, result={"ok": True})
            return

        if method in {"execute_tool", "execute_scoped_tool"}:
            name = params.get("name")
            if not name:
                raise ValueError("Missing 'name' in params")
            # 与 get_schemas_for_llm 同源：渲染层/直调不得绕过 toolsets.disabled。
            skill_scope = SkillScope.parse(params.get("skill_scope")) if method == "execute_scoped_tool" else None
            disabled_ids = get_disabled_toolset_ids()
            if name in excluded_tool_names(disabled_ids, {name}):
                raise ToolError(f"Tool '{name}' is disabled by toolsets.disabled")
            req_id_str = str(req_id) if req_id is not None else f"_anon_{uuid.uuid4().hex[:8]}"
            token = CancellationToken()
            _ACTIVE_CANCELLATIONS[req_id_str] = token
            ctx_reset = set_current_request(req_id_str)
            scope_reset = CURRENT_SKILL_SCOPE.set(skill_scope)
            cur_task = asyncio.current_task()
            _INFLIGHT_BY_REQ_ID[req_id_str] = cur_task
            _CURRENT_EXECUTE_TASK = cur_task
            _CURRENT_EXECUTE_REQ_ID = req_id_str
            try:
                try:
                    result = await registry.async_dispatch(name, params.get("args", {}), cancel_token=token)
                except ToolError as e:
                    await _send(ws, req_id, error={"code": -32000, "message": str(e)})
                    return
                await _send(ws, req_id, result=result)
                return
            finally:
                CURRENT_SKILL_SCOPE.reset(scope_reset)
                if _CURRENT_EXECUTE_TASK is cur_task:
                    _CURRENT_EXECUTE_TASK = None
                    _CURRENT_EXECUTE_REQ_ID = None
                _INFLIGHT_BY_REQ_ID.pop(req_id_str, None)
                _ACTIVE_CANCELLATIONS.pop(req_id_str, None)
                set_local_interrupt(req_id_str, False)
                with contextlib.suppress(ValueError):
                    reset_current_request(ctx_reset)

        await _send(ws, req_id, error={"code": -32601, "message": "Method not found"})
    except asyncio.CancelledError:
        cur_t = asyncio.current_task()
        if cur_t is not None:
            cur_t.uncancel()
        with contextlib.suppress(Exception):
            await _send(ws, req_id, error={"code": -32000, "message": "cancelled"})
        raise
    except Exception as e:
        # 回复本身绝不能再抛: 中途断连的 handler 会让后台任务以未捕获异常死去。
        with contextlib.suppress(Exception):
            await _send(ws, req_id, error={"code": -32000, "message": str(e)})


def _fail_pending_rpcs(reason: str) -> None:
    """对所有 in-flight 的 ``request_llm`` future 抛失败, 让调用方的 ``wait_for`` 迅速返回。

    用 ``set_exception`` 而不是 cancel 是为了把断连原因透给 LLM。future 可能在 values() 快照到迭代之间已经
    done(响应被取走 / ``wait_for`` 被取消), 在已 done 的 future 上 ``set_exception`` 会抛 InvalidStateError。
    """
    for fut in list(_PENDING_RPC.values()):
        if not fut.done():
            fut.set_exception(ConnectionError(reason))
    _PENDING_RPC.clear()


async def runner_loop(endpoint: DesktopEndpoint) -> None:
    """与 Desktop IPC 的长连接主循环: 维护持久连接、处理重连退避、派发 RPC、Ready 时主动通知。"""
    global _ACTIVE_WS, _RUNNER_LOOP, _RECONNECT_COUNT, _current_reconnect_streak

    current_endpoint = endpoint
    attempt = 0

    while True:
        cancelled = False
        # 等 Desktop 发布 endpoint(重启窗口、文件缺失/陈旧)是正常稳态, 不是连接失败 — 不能消耗下面的重连预算。
        tried_connect = current_endpoint is not None
        if current_endpoint is None:
            logger.info("Desktop endpoint not ready, waiting for endpoint file...")
        else:
            logger.info(f"Connecting to Desktop IPC: {current_endpoint.path} (attempt {attempt + 1})")
            # 新连接打开前主动 drain 掉残留的 ``_PENDING_RPC`` future。前一条连接的 ``finally`` 也会做,
            # 但要等 websocket 上下文完全退出之后 — 那窗口里新建的 future 会让调用方 ``wait_for`` 泄漏。
            # 廉价保险: 给所有尚未 done 的 future 抛 ConnectionError。
            _fail_pending_rpcs("Runner WS reconnecting; abandoning in-flight request")
            try:
                connection = await connect_desktop(current_endpoint)
                async with connection as ws:
                    _ACTIVE_WS = ws
                    _RUNNER_LOOP = asyncio.get_running_loop()
                    set_main_loop(_RUNNER_LOOP)
                    reset_llm_rate_limits()
                    ready_payload = await _runner_ready_payload()
                    await _send_notification(ws, "runner_ready", ready_payload)
                    attempt = 0
                    _current_reconnect_streak = 0
                    try:
                        async for message in ws:
                            try:
                                data = json.loads(message)
                            except json.JSONDecodeError:
                                logger.error("Invalid JSON received")
                                continue
                            # JSON-RPC 帧必须是 JSON 对象。解析成 ``int`` / ``list`` / ``None`` 的帧是畸形 — 打 log 跳过, 防止 ``data.get(...)`` 抛异常把读取任务杀掉。
                            if not isinstance(data, dict):
                                logger.error("Non-object JSON frame received: %r", type(data).__name__)
                                continue

                            req_id = data.get("id")
                            if req_id is not None and (fut := _PENDING_RPC.pop(req_id, None)):
                                if "error" in data:
                                    err = data["error"]
                                    err_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                                    fut.set_exception(Exception(err_msg))
                                else:
                                    fut.set_result(data.get("result"))
                                continue

                            t = asyncio.create_task(process_request(ws, data))
                            _BG_TASKS.add(t)
                            t.add_done_callback(_BG_TASKS.discard)
                    finally:
                        _ACTIVE_WS = None
                        _RUNNER_LOOP = None
                        # 排空待发 request_llm future, 让调用方 ``wait_for`` 携带断连原因迅速返回。
                        _fail_pending_rpcs("Runner WS disconnected before response arrived")

            except websockets.exceptions.ConnectionClosed:
                logger.warning("WebSocket connection closed by Desktop.")
            except websockets.exceptions.InvalidStatus as e:
                # Desktop 升级前 token 校验返回 HTTP 401: 本进程仍持有上一个会话的 token(Desktop 重启过)。
                # 丢弃缓存 endpoint, 下一次从 endpoint 文件重读新的路径+token — 重试旧的一定 401 白白烧掉重试预算。
                logger.warning(f"Desktop rejected handshake ({e.response.status_code}); refreshing endpoint")
                current_endpoint = None
            except asyncio.CancelledError:
                # 测试清理 / 正常退出 — 离开循环, 不 bump ``_RECONNECT_COUNT``, 因为这是主动拆除, 不是会话中途掉线。
                cancelled = True
            except Exception as e:
                logger.error(f"WebSocket error: {e}")

        if cancelled:
            break

        # 从 Desktop 写的文件读取最新 endpoint(路径+token); 文件缺失/陈旧就保留缓存的 endpoint。
        new_endpoint = read_endpoint()
        if new_endpoint:
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


async def _runner_ready_payload() -> dict[str, Any]:
    """编译 ``runner_ready`` 通知负载: Desktop 用它决定要不要暴露依赖可选 OS 子系统的功能(麦克风、截屏、system activity、本地 STT/TTS)。

    探测失败时返回结构上独立于 ``capabilities={...all False}`` 的形态(``probe_failed=True``), 让 Desktop 能区分;
    Desktop 应当把任何 ``capabilities`` 里缺失的键视为"不启用", 不管 ``probe_failed`` 是什么。
    """
    payload: dict[str, Any] = {
        "version": __version__,
        "capabilities": {},
        "capabilities_health": {},
        "probe_failed": False,
        "reconnect_streak": _current_reconnect_streak,
    }
    try:
        caps = await asyncio.to_thread(snapshot)
        if isinstance(caps, dict):
            payload["capabilities"] = caps
            try:
                payload["capabilities_health"] = await asyncio.to_thread(snapshot_health)
            except Exception:
                payload["capabilities_health"] = {}
        else:
            payload["probe_failed"] = True
    except Exception as e:
        logger.warning(f"capabilities probe failed: {e}")
        payload["probe_failed"] = True
    return payload


async def _build_info() -> dict[str, Any]:
    """``spiritagent.info`` RPC 的完整快照: 除能力外还含进程 / OS 状态, 让 Desktop 诊断面板分得清"陈旧进程"与"冷启动", 也让 agent 自己能据此调整行为(例如磁盘吃紧时避免重型工具)。"""
    try:
        caps = await asyncio.to_thread(snapshot)
        if not isinstance(caps, dict):
            caps = {}
    except Exception:
        caps = {}
    try:
        health = await asyncio.to_thread(snapshot_health)
        if not isinstance(health, dict):
            health = {}
    except Exception:
        health = {}
    try:
        import_failures = registry.get_import_failures()
        if import_failures:
            health["failed_tools"] = import_failures
    except Exception:
        pass
    try:
        tool_names = registry.get_all_tool_names()
    except Exception:
        tool_names = []
    # ``network_reachable`` 可能阻塞 ~1.5s; 放到线程里避免探测期间心跳 / spiritagent.cancel 失去响应。
    reachable = await asyncio.to_thread(network_reachable)
    return {
        "version": __version__,
        "started_at": _STARTED_AT,
        "uptime_seconds": round(time.time() - _STARTED_AT, 2),
        "reconnect_count": _RECONNECT_COUNT,
        "capabilities": caps,
        "capabilities_health": health,
        "system": {
            "platform": sys.platform,
            "python": sys.version.split()[0],
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "tool_count": len(tool_names),
        "network_reachable": reachable,
        "disk_free_bytes": disk_free_bytes(get_spiritagent_home()),
    }


def main() -> None:
    _require_supported_host()
    init_runner_job_object()
    parser = argparse.ArgumentParser(description="SpiritAgent Runner Server")
    parser.add_argument(
        "--desktop-endpoint",
        required=True,
        help="Desktop IPC path (Windows named pipe or Unix socket path)",
    )
    parser.add_argument(
        "--desktop-auth",
        default="",
        help="Desktop handshake token (prefer SPIRITAGENT_DESKTOP_TOKEN env; argv is visible in process lists)",
    )
    args = parser.parse_args()

    # token 优先经环境变量下发，避免出现在同机进程列表的 argv 中。
    token = os.environ.get("SPIRITAGENT_DESKTOP_TOKEN") or args.desktop_auth
    if not token:
        parser.error("Desktop handshake token is required (SPIRITAGENT_DESKTOP_TOKEN or --desktop-auth)")

    transport = "pipe" if sys.platform == "win32" else "unix"
    endpoint = DesktopEndpoint(transport=transport, path=args.desktop_endpoint, token=token)

    imported, import_errors = discover_builtin_tools_strict()
    if import_errors:
        logger.warning("Some builtin tool modules failed to load: %s", import_errors)
    set_handler(request_llm_from_desktop)

    try:
        asyncio.run(runner_loop(endpoint))
    except KeyboardInterrupt:
        logger.info("Runner stopped.")


if __name__ == "__main__":
    main()
