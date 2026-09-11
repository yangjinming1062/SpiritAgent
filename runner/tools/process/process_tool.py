import contextlib
import json
import logging
import os
import queue
import shlex
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from envs import register_active_process_checker
from utils import (
    CREATE_NO_WINDOW,
    IS_WINDOWS,
    atomic_replace,
    cfg_get,
    clean_output,
    find_bash,
    get_spiritagent_home,
    is_interrupted,
    load_config,
    pid_exists,
    redact_sensitive_text,
    resolve_safe_cwd,
    sanitize_subprocess_env,
    terminate_tree,
)

from ..registry import registry, tool_error

if IS_WINDOWS:
    from winpty import PtyProcess as _PtyProcessCls
else:
    from ptyprocess import PtyProcess as _PtyProcessCls

# ``fcntl`` 仅 POSIX 标准库, Windows 编译期就没有, try/except 也会永远抛; 在 POSIX 上无条件 import。
# (下面的 fcntl 代码路径只为 ptyprocess 输出用, 而 ptyprocess 本身也是 POSIX-only, 因此 Windows 根本走不到。)
if not IS_WINDOWS:
    import fcntl
else:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.PeekNamedPipe.argtypes = [
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.LPVOID,
    ]
    _kernel32.PeekNamedPipe.restype = wintypes.BOOL


def _drain_pipe_peek_windows(stdout) -> str:
    """一次性把 pipe 当前可读字节全部抽走(非阻塞)。

    PeekNamedPipe 先报可读字节数, 这样即使派生进程仍在持有句柄, 后面的 ``os.read`` 也不会阻塞。
    """
    chunks: list[str] = []
    try:
        fd = stdout.fileno()
        handle = msvcrt.get_osfhandle(fd)
        avail = wintypes.DWORD(0)
        while _kernel32.PeekNamedPipe(handle, None, 0, None, ctypes.byref(avail), None) and avail.value > 0:
            chunk = os.read(fd, min(65536, avail.value))
            if not chunk:
                break
            chunks.append(chunk.decode("utf-8", errors="replace"))
            avail = wintypes.DWORD(0)
    except (OSError, ValueError) as e:
        logger.debug("PeekNamedPipe drain failed: %s", e)
    return "".join(chunks)


logger = logging.getLogger(__name__)

# 用于崩溃恢复的 checkpoint 文件。
CHECKPOINT_PATH = get_spiritagent_home() / "processes.json"

MAX_OUTPUT_CHARS = 200_000  # 200KB 滚动输出缓冲
FINISHED_TTL_SECONDS = 1800  # 已结束的进程保留 30 分钟
MAX_PROCESSES = 64  # 同时跟踪的进程上限, LRU 淘汰

# Watch 模式限流 — 每 session 维度。
# 硬规则: 两条 watch-match 通知的间隔至少 WATCH_MIN_INTERVAL_SECONDS。
# 在冷却窗口内的匹配会被丢弃并计为一次 strike。连续 WATCH_STRIKE_LIMIT 个 strike 窗口后,
# 改用 notify_on_complete(进程退出时一次通知, 不再中途刷屏)。
WATCH_MIN_INTERVAL_SECONDS = 15  # 两次匹配通知之间最小间隔(秒)
WATCH_STRIKE_LIMIT = 3  # 连续 strike 数阈值, 超后升级为 notify_on_complete

# 全局熔断器 — 跨 session 维度。第二层安全网, 让多个 session 协同起来也不能淹没用户(每条各自限流不足以防整体洪水)。
WATCH_GLOBAL_MAX_PER_WINDOW = 15
WATCH_GLOBAL_WINDOW_SECONDS = 10
WATCH_GLOBAL_COOLDOWN_SECONDS = 30


@dataclass
class ProcessSession:
    """一个被跟踪的后台进程, 带输出缓冲。"""

    id: str  # 唯一会话 ID("proc_xxxxxxxxxxxx")
    command: str  # 原始命令字符串
    task_id: str = ""  # 任务 / 沙箱隔离键
    session_key: str = ""  # Gateway 会话键(防止 reset 误删)
    pid: int | None = None  # 操作系统进程 ID
    process: subprocess.Popen | None = None  # Popen handle(仅本机)
    env_ref: Any = None  # 关联的 Environment 对象
    cwd: str | None = None  # 工作目录
    started_at: float = 0.0  # spawn 时的 time.time()
    exited: bool = False  # 进程是否已结束
    exit_code: int | None = None  # 退出码(未结束则为 None)
    output_buffer: str = ""  # 滚动输出(最近 MAX_OUTPUT_CHARS)
    max_output_chars: int = MAX_OUTPUT_CHARS
    detached: bool = False  # 崩溃恢复得到的, 没有 stdout 管道
    pid_scope: str = "host"  # "host" 为本机 / PTY 的 PID, "sandbox" 为 env 内 PID
    # watcher / 通知元数据(崩溃恢复需要持久化)。
    watcher_platform: str = ""
    watcher_chat_id: str = ""
    watcher_user_id: str = ""
    watcher_user_name: str = ""
    watcher_thread_id: str = ""
    watcher_message_id: str = ""  # 触发的消息 ID — 主题路由的回复锚点
    watcher_interval: int = 0  # 0 表示未配置 watcher
    notify_on_complete: bool = False  # 退出时入队 agent 通知
    # watch_patterns — 输出匹配任意模式时触发 agent 通知。
    watch_patterns: list[str] = field(default_factory=list)
    _watch_hits: int = field(default=0, repr=False)  # 实际下发的匹配数
    _watch_suppressed: int = field(default=0, repr=False)  # 被限流丢弃的匹配
    _watch_disabled: bool = field(default=False, repr=False)  # 触发 strike 限制后永久停用
    # 每 session 限流状态: 两次匹配至少 WATCH_MIN_INTERVAL_SECONDS。
    # 一次下发后, ``_watch_cooldown_until`` 设为 now + interval; 在截止时间之前来的任何匹配
    # 都计为一次 strike(无论中间丢过多少 — strike 是窗口维度而不是匹配维度)。连续 WATCH_STRIKE_LIMIT
    # 个 strike 后停用 watch_patterns, 提升为 notify_on_complete 语义。
    _watch_last_emit_at: float = field(default=0.0, repr=False)
    _watch_cooldown_until: float = field(default=0.0, repr=False)
    _watch_strike_candidate: bool = field(default=False, repr=False)
    _watch_consecutive_strikes: int = field(default=0, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _reader_thread: threading.Thread | None = field(default=None, repr=False)
    _pty: Any = field(default=None, repr=False)  # ptyprocess 句柄(use_pty=True 时)


class PTYBufferFull(OSError):
    """PTY 或标准输入写入缓冲区已满且在背压超时内未能消费。"""


class ProcessRegistry:
    """运行中 / 已结束后台进程的内存注册表 — 线程安全。

    接入方:
      - 执行线程(terminal_tool / process 工具 handler)
      - Gateway asyncio loop(watcher 任务 / session reset 检查)
      - 清理线程(沙箱回收协调)
    """

    _SHELL_NOISE_SUBSTRINGS = (
        "bash: cannot set terminal process group",
        "bash: no job control in this shell",
        "no job control in this shell",
        "cannot set terminal process group",
        "tcsetattr: Inappropriate ioctl for device",
    )

    def __init__(self) -> None:
        self._running: dict[str, ProcessSession] = {}
        self._finished: dict[str, ProcessSession] = {}
        self._lock = threading.Lock()
        # check_interval watcher 的侧信道: gateway 在 agent 运行结束后读取它。
        self.pending_watchers: list[dict[str, Any]] = []
        # 通知队列 — 所有后台进程事件的统一队列。
        # 完成通知(notify_on_complete)和 watch 模式匹配都进这里, 通过 "type" 字段区分。
        # CLI process_loop 和 gateway 在每次 agent 回合结束后 drain, 自动触发下一轮。
        self.completion_queue: queue.Queue = queue.Queue()
        # 已经被 wait/poll/log 直接消费的会话: drain 循环对它们跳过完成通知。
        self._completion_consumed: set = set()
        # 跨 session 的全局 watch-match 熔断器 — 防止单 session 各自限流依然整体洪泛用户。
        self._global_watch_lock = threading.Lock()
        self._global_watch_window_start: float = 0.0
        self._global_watch_window_hits: int = 0
        self._global_watch_tripped_until: float = 0.0
        self._global_watch_suppressed_during_trip: int = 0

    @staticmethod
    def _clean_shell_noise(text: str) -> str:
        """剥掉输出开头几行的 shell 启动告警(交互式 shell 标志等)。"""
        lines = text.split("\n")
        while lines and any(noise in lines[0] for noise in ProcessRegistry._SHELL_NOISE_SUBSTRINGS):
            lines.pop(0)
        return "\n".join(lines)

    def _check_watch_patterns(self, session: ProcessSession, new_text: str) -> None:
        """扫描新增输出匹配 watch_patterns, 按限流规则入队通知(reader 线程调用)。

        每 session 限流: 两次 watch-match 通知至少 WATCH_MIN_INTERVAL_SECONDS; 冷却窗口内的所有匹配
        都丢掉并为本窗口记 1 次 strike。连续 WATCH_STRIKE_LIMIT 个 strike 窗口后, 本 session 停用
        ``watch_patterns``, 提升到 ``notify_on_complete`` 语义 — 进程退出时一次通知, 之后再无中途刷屏。
        """
        if not session.watch_patterns or session._watch_disabled:
            return
        # 退出后抑制: reader 循环宣告进程退出后再出现的延迟 chunk 都是退出后噪声;
        # 直接丢弃防住 "进程结束后几分钟还在发陈旧通知" 的体验问题。
        if session.exited:
            return
        matched_lines = []
        matched_pattern = None
        for line in new_text.splitlines():
            for pat in session.watch_patterns:
                if pat in line:
                    matched_lines.append(line.rstrip())
                    if matched_pattern is None:
                        matched_pattern = pat
                    break  # 每行算一次匹配
        if not matched_lines:
            return
        now = time.time()
        should_disable = False
        with session._lock:
            # 情形 1: 仍在上一发冷却期内 — 计本窗口 1 次 strike 并丢弃;
            # 累计达到上限时停用 watch 并提升为 notify_on_complete。
            if session._watch_cooldown_until and now < session._watch_cooldown_until:
                session._watch_suppressed += len(matched_lines)
                if not session._watch_strike_candidate:
                    # 本窗口首次丢弃 — 记一次 strike。
                    session._watch_strike_candidate = True
                    session._watch_consecutive_strikes += 1
                    if session._watch_consecutive_strikes >= WATCH_STRIKE_LIMIT:
                        session._watch_disabled = True
                        # 进程真正退出时再补一次通知, 不再中途刷屏。
                        session.notify_on_complete = True
                        should_disable = True
                return_early = True
            else:
                # 情形 2: 冷却已过。
                # 视本窗口是否"干净"(无丢弃)或 strike 窗口: 上一冷却内没记 strike 就重置连续 strike 计数,
                # 恢复健康下发节奏。
                if session._watch_cooldown_until and not session._watch_strike_candidate:
                    session._watch_consecutive_strikes = 0
                session._watch_strike_candidate = False
                session._watch_last_emit_at = now
                session._watch_cooldown_until = now + WATCH_MIN_INTERVAL_SECONDS
                session._watch_hits += 1
                suppressed = session._watch_suppressed
                session._watch_suppressed = 0
                return_early = False
        if return_early:
            if should_disable:
                # 发一条 watch_disabled 汇总事件让 agent / 用户看到"为什么安静了"。
                msg = self._watcher_event_base(session)
                msg.update(
                    {
                        "type": "watch_disabled",
                        "suppressed": session._watch_suppressed,
                        "message": (
                            f"Watch patterns disabled for process {session.id} — "
                            f"{WATCH_STRIKE_LIMIT} consecutive rate-limit windows "
                            f"triggered (min spacing {WATCH_MIN_INTERVAL_SECONDS}s). "
                            f"Falling back to notify_on_complete semantics; you'll get "
                            f"exactly one notification when the process exits."
                        ),
                    },
                )
                self.completion_queue.put(msg)
            return

        output = clean_output("\n".join(matched_lines[:20]))
        if len(output) > 2000:
            output = output[:2000] + "\n...(truncated)"
        # 全局熔断器 — 跨 session 维度的第二层安全网。
        if not self._global_watch_admit(now):
            return
        msg = self._watcher_event_base(session)
        msg.update({"type": "watch_match", "pattern": matched_pattern, "output": output, "suppressed": suppressed})
        self.completion_queue.put(msg)

    def _watcher_event_base(self, session: ProcessSession) -> dict[str, str]:
        return {
            "session_id": session.id,
            "session_key": session.session_key,
            "command": session.command,
            "platform": session.watcher_platform,
            "chat_id": session.watcher_chat_id,
            "user_id": session.watcher_user_id,
            "user_name": session.watcher_user_name,
            "thread_id": session.watcher_thread_id,
            "message_id": session.watcher_message_id,
        }

    def _global_watch_admit(self, now: float) -> bool:
        """全局熔断闸门: True 表示允许本次 watch_match 事件通过。

        - 当前在冷却期内: 直接丢弃并计数。
        - 否则滑动窗口, 命中 WATCH_GLOBAL_MAX_PER_WINDOW 上限就熔断
            WATCH_GLOBAL_COOLDOWN_SECONDS, 同时下发一条汇总事件
            ("N notifications were suppressed")代替逐条刷屏。
        - 冷却结束再发一条 release 汇总并清零计数。
        """
        with self._global_watch_lock:
            if self._global_watch_tripped_until and now >= self._global_watch_tripped_until:
                suppressed = self._global_watch_suppressed_during_trip
                self._global_watch_tripped_until = 0.0
                self._global_watch_suppressed_during_trip = 0
                self._global_watch_window_start = now
                self._global_watch_window_hits = 0
                if suppressed > 0:
                    # 汇总事件放在锁外入队。
                    release_msg = {
                        "session_id": "",
                        "session_key": "",
                        "command": "",
                        "type": "watch_overflow_released",
                        "suppressed": suppressed,
                        "message": (
                            f"Watch-pattern notifications resumed. "
                            f"{suppressed} match event(s) were suppressed during the flood."
                        ),
                        "platform": "",
                        "chat_id": "",
                        "user_id": "",
                        "user_name": "",
                        "thread_id": "",
                    }
                else:
                    release_msg = None
            else:
                release_msg = None
            # 仍在冷却中 — 丢弃并计数。
            if self._global_watch_tripped_until and now < self._global_watch_tripped_until:
                self._global_watch_suppressed_during_trip += 1
                admit = False
                trip_now = None
            else:
                # 滑动窗口。
                if now - self._global_watch_window_start >= WATCH_GLOBAL_WINDOW_SECONDS:
                    self._global_watch_window_start = now
                    self._global_watch_window_hits = 0
                if self._global_watch_window_hits >= WATCH_GLOBAL_MAX_PER_WINDOW:
                    # 触发熔断。触发事件本身不计为 suppressed; 只有冷却窗口里到达的才计。
                    # (self._global_watch_tripped_until 在下一行设置。)
                    self._global_watch_tripped_until = now + WATCH_GLOBAL_COOLDOWN_SECONDS
                    trip_now = now
                    admit = False
                else:
                    self._global_watch_window_hits += 1
                    trip_now = None
                    admit = True
        # 把汇总事件放在锁外入队。
        if release_msg is not None:
            self.completion_queue.put(release_msg)
        if trip_now is not None:
            self.completion_queue.put(
                {
                    "session_id": "",
                    "session_key": "",
                    "command": "",
                    "type": "watch_overflow_tripped",
                    "message": (
                        f"Watch-pattern overflow: >{WATCH_GLOBAL_MAX_PER_WINDOW} "
                        f"notifications in {WATCH_GLOBAL_WINDOW_SECONDS}s across all "
                        f"processes. Suppressing further watch_match events for "
                        f"{WATCH_GLOBAL_COOLDOWN_SECONDS}s."
                    ),
                    "platform": "",
                    "chat_id": "",
                    "user_id": "",
                    "user_name": "",
                    "thread_id": "",
                },
            )
        return admit

    @staticmethod
    def _is_host_pid_alive(pid: int | None) -> bool:
        """尽力探测 host 可见 PID 是否存活; 调用跨平台 ``utils.pid.pid_exists`` —
        Windows 上 ``os.kill(pid, 0)`` 并非 no-op(bpo-14484)。
        """
        return bool(pid) and pid_exists(pid)

    def _refresh_detached_session(self, session: ProcessSession | None) -> ProcessSession | None:
        """当崩溃恢复得到的 host PID session 对应的真实进程已经退出时, 把它的 exited 字段翻正。"""
        if session is None or session.exited or not session.detached or session.pid_scope != "host":
            return session
        if self._is_host_pid_alive(session.pid):
            return session
        with session._lock:
            if session.exited:
                return session
            session.exited = True
            # 恢复出来的 session 已经没有 waitable 句柄, 一旦原始 Popen 对象消失就拿不到真实 exit code。
            session.exit_code = None
        self._move_to_finished(session)
        return session

    @staticmethod
    def _terminate_host_pid(pid: int) -> None:
        """终止一个 host 可见 PID 及其所有后代进程.

        POSIX 走 ``utils.process_tree.terminate_tree`` (pgid 优先, psutil 兜底);
        Windows 委派 ``utils.pid.kill_tree`` (``taskkill /T /F``)。
        """
        # host-PID 场景通常没有 stashed pgid, helper 内部通过 ``os.getpgid`` 探测;
        # ``escalate=False`` 与旧实现保持一致 —— 软杀超时后 psutil 兜底 SIGKILL 残存 PID,
        # 不再为本地活跃分支做额外的 SIGKILL 升级。
        try:
            terminate_tree(pid, graceful_timeout=0.5, force_timeout=1.0)
        except (ProcessLookupError, PermissionError, OSError):
            with contextlib.suppress(OSError, ProcessLookupError, PermissionError):
                os.kill(pid, signal.SIGTERM)

    # ----- Spawn -----
    @staticmethod
    def _env_temp_dir(env: Any) -> str:
        """返回 env 后端后台任务可写的沙箱临时目录。"""
        get_temp_dir = getattr(env, "get_temp_dir", None)
        if callable(get_temp_dir):
            try:
                temp_dir = get_temp_dir()
                if isinstance(temp_dir, str) and temp_dir.startswith("/"):
                    return temp_dir.rstrip("/") or "/"
            except Exception as exc:
                logger.debug("Could not resolve environment temp dir: %s", exc)
        return "/tmp"

    def spawn_local(
        self,
        command: str,
        cwd: str | None = None,
        task_id: str = "",
        session_key: str = "",
        env_vars: dict | None = None,
        use_pty: bool = False,
    ) -> ProcessSession:
        """本机派生一个后台进程。仅限 TERMINAL_ENV=local; 其他后端走 ``spawn_via_env()``。

        ``use_pty=True`` 用 ptyprocess 打开伪终端, 给交互式 CLI(Codex / Claude Code / Python REPL)用;
        ptyprocess 缺失则回落到 ``subprocess.Popen``。
        """
        session = ProcessSession(
            id=f"proc_{uuid.uuid4().hex[:12]}",
            command=command,
            task_id=task_id,
            session_key=session_key,
            cwd=resolve_safe_cwd(cwd or os.getcwd()),
            started_at=time.time(),
        )
        if use_pty:
            try:
                user_shell = find_bash()
                pty_env = sanitize_subprocess_env(os.environ, env_vars)
                pty_env["PYTHONUNBUFFERED"] = "1"
                pty_proc = _PtyProcessCls.spawn(
                    [user_shell, "-lic", f"set +m; {command}"],
                    cwd=session.cwd,
                    env=pty_env,
                    dimensions=(30, 120),
                )
                session.pid = pty_proc.pid
                session._pty = pty_proc
                reader = threading.Thread(
                    target=self._pty_reader_loop,
                    args=(session,),
                    daemon=True,
                    name=f"proc-pty-reader-{session.id}",
                )
                session._reader_thread = reader
                reader.start()
                with self._lock:
                    self._prune_if_needed()
                    self._running[session.id] = session
                self._write_checkpoint()
                return session
            except ImportError:
                logger.warning("ptyprocess not installed, falling back to pipe mode")
            except Exception as e:
                logger.warning("PTY spawn failed (%s), falling back to pipe mode", e)
        # 标准 Popen 路径(非 PTY 或 PTY 回落)。用用户登录 shell, 与 LocalEnvironment 保持一致 —
        # 确保 rc 文件生效且用户工具可用。
        user_shell = find_bash()
        # 强制 PYTHONUNBUFFERED=1, 否则 tqdm / datasets 这类库在 pipe stdout 时会缓冲,
        # 导致 process(poll) 看不到任何输出。
        bg_env = sanitize_subprocess_env(os.environ, env_vars)
        bg_env["PYTHONUNBUFFERED"] = "1"
        _popen_kwargs = {"creationflags": CREATE_NO_WINDOW} if IS_WINDOWS else {}
        proc = subprocess.Popen(
            [user_shell, "-lic", f"set +m; {command}"],
            text=True,
            cwd=session.cwd,
            env=bg_env,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE,
            preexec_fn=None if IS_WINDOWS else os.setsid,
            **_popen_kwargs,
        )
        session.process = proc
        session.pid = proc.pid
        try:
            reader = threading.Thread(
                target=self._reader_loop,
                args=(session,),
                daemon=True,
                name=f"proc-reader-{session.id}",
            )
            session._reader_thread = reader
            reader.start()
            with self._lock:
                self._prune_if_needed()
                self._running[session.id] = session
            self._write_checkpoint()
        except Exception:
            # Popen 之后启动失败 — 把孤儿子进程连同 setsid 派生的后代一起杀掉再上抛, 否则会留下不可见的野进程。
            try:
                # 异常路径内的清理, 必须 try/except 包裹, 任何新抛错被外层 ``except: pass`` 吞掉。
                # ``escalate=True`` 与原 inline SIGKILL-on-group 行为对齐 —— 既然进程已经"启动失败",
                # 不需要给它留 graceful_timeout, 直接强杀整组 + psutil 兜底。
                terminate_tree(proc, graceful_timeout=0.5, force_timeout=1.0, escalate=True)
            except Exception:
                with contextlib.suppress(Exception):
                    proc.kill()
            with contextlib.suppress(Exception):
                proc.wait(timeout=5)
            raise
        return session

    def spawn_via_env(
        self,
        env: Any,
        command: str,
        cwd: str | None = None,
        task_id: str = "",
        session_key: str = "",
        timeout: int = 10,
    ) -> ProcessSession:
        """通过 SSH Environment 后端派生后台进程。

        把命令包成 nohup, 把 in-sandbox PID 和输出重定向到沙箱内的 log 文件, 随后通过后续 ``execute()`` 轮询状态。
        不支持实时 stdout pipe 和 stdin 输入 — 但能保证命令跑在正确的 sandbox 上下文里。
        """
        session = ProcessSession(
            id=f"proc_{uuid.uuid4().hex[:12]}",
            command=command,
            task_id=task_id,
            session_key=session_key,
            cwd=cwd,
            started_at=time.time(),
            env_ref=env,
            pid_scope="sandbox",
        )
        temp_dir = self._env_temp_dir(env)
        log_path = f"{temp_dir}/spiritagent_bg_{session.id}.log"
        pid_path = f"{temp_dir}/spiritagent_bg_{session.id}.pid"
        exit_path = f"{temp_dir}/spiritagent_bg_{session.id}.exit"
        quoted_command = shlex.quote(command)
        quoted_temp_dir = shlex.quote(temp_dir)
        quoted_log_path = shlex.quote(log_path)
        quoted_pid_path = shlex.quote(pid_path)
        quoted_exit_path = shlex.quote(exit_path)
        bg_command = (
            f"mkdir -p {quoted_temp_dir} && "
            f"( nohup bash -lc {quoted_command} > {quoted_log_path} 2>&1; "
            f"rc=$?; printf '%s\\n' \"$rc\" > {quoted_exit_path} ) & "
            f"echo $! > {quoted_pid_path} && cat {quoted_pid_path}"
        )
        try:
            result = env.execute(bg_command, timeout=timeout, rewrite_compound_background=False)
            output = result.get("output", "").strip()
            for line in output.splitlines():
                line = line.strip()
                if line.isdigit():
                    session.pid = int(line)
                    break
            # 包壳若没产出 PID(语法错误 / 重定向失败), 视为启动失败而不是伪装一个 in-flight session。
            if session.pid is None:
                session.exited = True
                session.exit_code = int(result.get("returncode", -1))
                if session.exit_code == 0:
                    session.exit_code = -1
                session.output_buffer = result.get("output", "").strip()
        except Exception as e:
            session.exited = True
            session.exit_code = -1
            session.output_buffer = f"Failed to start: {e}"
        if not session.exited:
            reader = threading.Thread(
                target=self._env_poller_loop,
                args=(session, env, log_path, pid_path, exit_path),
                daemon=True,
                name=f"proc-poller-{session.id}",
            )
            session._reader_thread = reader
            reader.start()
        with self._lock:
            self._prune_if_needed()
            if not session.exited:
                self._running[session.id] = session
        if not session.exited:
            self._write_checkpoint()
        return session

    # ----- Reader / Poller Threads -----
    def _reader_loop(self, session: ProcessSession) -> None:
        """后台线程: 从本机 Popen 进程的 stdout 读数据并维护 output_buffer。"""
        first_chunk = True
        try:
            while True:
                chunk = session.process.stdout.read(4096)
                if not chunk:
                    break
                if first_chunk:
                    chunk = self._clean_shell_noise(chunk)
                    first_chunk = False
                with session._lock:
                    session.output_buffer += chunk
                    if len(session.output_buffer) > session.max_output_chars:
                        session.output_buffer = session.output_buffer[-session.max_output_chars :]
                self._check_watch_patterns(session, chunk)
        except Exception as e:
            logger.debug("Process stdout reader ended: %s", e)
        finally:
            # 始终 reap 子进程以避免僵尸进程。
            try:
                session.process.wait(timeout=5)
            except Exception as e:
                logger.debug("Process wait timed out or failed: %s", e)
            session.exited = True
            session.exit_code = session.process.returncode
            self._move_to_finished(session)

    def _env_poller_loop(self, session: ProcessSession, env: Any, log_path: str, pid_path: str, exit_path: str) -> None:
        """后台线程: 给非本地后端轮询沙箱内的 log 文件(每 2 秒一次)。"""
        quoted_log_path = shlex.quote(log_path)
        quoted_pid_path = shlex.quote(pid_path)
        quoted_exit_path = shlex.quote(exit_path)
        prev_output_len = 0
        while not session.exited:
            time.sleep(2)
            try:
                result = env.execute(f"cat {quoted_log_path} 2>/dev/null", timeout=10)
                new_output = result.get("output", "")
                if new_output:
                    delta = new_output[prev_output_len:] if len(new_output) > prev_output_len else ""
                    prev_output_len = len(new_output)
                    with session._lock:
                        session.output_buffer = new_output
                        if len(session.output_buffer) > session.max_output_chars:
                            session.output_buffer = session.output_buffer[-session.max_output_chars :]
                    if delta:
                        self._check_watch_patterns(session, delta)
                check = env.execute(f'kill -0 "$(cat {quoted_pid_path} 2>/dev/null)" 2>/dev/null; echo $?', timeout=5)
                check_output = check.get("output", "").strip()
                if check_output and check_output.splitlines()[-1].strip() != "0":
                    exit_result = env.execute(f"cat {quoted_exit_path} 2>/dev/null", timeout=5)
                    exit_str = exit_result.get("output", "").strip()
                    try:
                        session.exit_code = int(exit_str.splitlines()[-1].strip())
                    except (ValueError, IndexError):
                        session.exit_code = -1
                    session.exited = True
                    self._move_to_finished(session)
                    return
            except Exception:
                # Environment 可能已经被回收(沙箱 reaped 等)。
                session.exited = True
                session.exit_code = -1
                self._move_to_finished(session)
                return

    def _pty_reader_loop(self, session: ProcessSession) -> None:
        """后台线程: 从 PTY 进程读输出。"""
        pty = session._pty
        try:
            while pty.isalive():
                try:
                    chunk = pty.read(4096)
                    if chunk:
                        text = chunk if isinstance(chunk, str) else chunk.decode("utf-8", errors="replace")
                        with session._lock:
                            session.output_buffer += text
                            if len(session.output_buffer) > session.max_output_chars:
                                session.output_buffer = session.output_buffer[-session.max_output_chars :]
                        self._check_watch_patterns(session, text)
                except EOFError:
                    break
                except Exception:
                    break
        except Exception as e:
            logger.debug("PTY stdout reader ended: %s", e)
        try:
            pty.wait()
        except Exception as e:
            logger.debug("PTY wait timed out or failed: %s", e)
        session.exited = True
        session.exit_code = pty.exitstatus if hasattr(pty, "exitstatus") else -1
        self._move_to_finished(session)

    def _move_to_finished(self, session: ProcessSession) -> None:
        """把一个 session 从 running 移到 finished; 幂等, 不会被并发调用重入双下发。"""
        with self._lock:
            was_running = self._running.pop(session.id, None) is not None
            self._finished[session.id] = session
        self._write_checkpoint()
        # 仅在第一次移过来时入队完成通知 — 不做这个守卫, kill_process() 和 reader 线程
        # 可能各自调用一次, 产出重复 ``[IMPORTANT: ...]`` 消息。
        if was_running and session.notify_on_complete:
            output_tail = clean_output(session.output_buffer[-2000:]) if session.output_buffer else ""
            self.completion_queue.put(
                {
                    "type": "completion",
                    "session_id": session.id,
                    "session_key": session.session_key,
                    "command": session.command,
                    "exit_code": session.exit_code,
                    "output": output_tail,
                },
            )

    # ----- Query Methods -----
    def is_completion_consumed(self, session_id: str) -> bool:
        """判断某个会话的完成通知是否已被 ``wait`` / ``poll`` / ``log`` 直接消费(用于 drain 时跳过)。"""
        return session_id in self._completion_consumed

    def drain_notifications(self) -> "list[tuple[dict, str]]":
        """从队列里取出所有待发的通知事件, 返回 ``(原始事件, 格式化文本)`` 列表;
        自动跳过已经被 wait/poll/log 消费过的完成事件。
        """
        results = []
        while not self.completion_queue.empty():
            try:
                evt = self.completion_queue.get_nowait()
            except Exception:
                break
            _evt_sid = evt.get("session_id", "")
            if evt.get("type") == "completion" and self.is_completion_consumed(_evt_sid):
                continue
            text = format_process_notification(evt)
            if text:
                results.append((evt, text))
        return results

    def get(self, session_id: str) -> ProcessSession | None:
        """根据 ID 取一个 session(运行中 / 已结束均可), 并对崩溃恢复得到的 detached session 做存活刷新。"""
        with self._lock:
            session = self._running.get(session_id) or self._finished.get(session_id)
        return self._refresh_detached_session(session)

    def _reconcile_local_exit(self, session: "ProcessSession") -> None:
        """把 ``session.exited`` 与真实子进程状态对账。

        reader 线程仅在 ``stdout.read()`` 返回 EOF 的 ``finally`` 里把 ``session.exited`` 置 True; 但若直接
        Popen 子进程已经退出, 而某个派生进程(例如自更新重启 gateway 派生的守护)还抓着 stdout pipe 不放, reader
        会无限阻塞, 外部 ``poll()`` 永远返回 "running"(issue #17327 — 飞书那边 7 分钟里 poll 了 74 次)。

        本方法把这个窗口关上: ``session.exited`` 仍是 False 但 ``Popen.poll()`` 已经返回 exit code 时,
        非阻塞把可读字节抽走, 然后把 ``exited`` 翻过去。被遗留的 reader 线程是 daemon, 进程退出时一同回收。

        对没有本地 Popen 的(env / PTY)、已退出的、以及 detached 恢复出的 session 都是安全的 no-op。
        """
        if session is None or session.exited:
            return
        proc = getattr(session, "process", None)
        if proc is None:
            return
        try:
            rc = proc.poll()
        except Exception:
            return
        if rc is None:
            return  # 直接子进程仍在跑 — reader 的阻塞是合规的。
        # 直接子进程已退出。尽力把 reader 没消费完的字节抽走: 若 pipe 被派生进程霸占,
        # 非阻塞读只会拿到当前可用的部分, 拿不到就停。
        drained = ""
        stdout = getattr(proc, "stdout", None)
        if stdout is not None and IS_WINDOWS:
            drained = _drain_pipe_peek_windows(stdout)
        elif stdout is not None:
            try:
                fd = stdout.fileno()
                flags = fcntl.fcntl(fd, fcntl.F_GETFL)
                fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
                try:
                    chunk = stdout.read()
                    if chunk:
                        drained = chunk if isinstance(chunk, str) else chunk.decode("utf-8", errors="replace")
                except (BlockingIOError, OSError, ValueError):
                    pass
                finally:
                    with contextlib.suppress(Exception):
                        fcntl.fcntl(fd, fcntl.F_SETFL, flags)
            except Exception as e:
                logger.debug("Non-blocking drain failed for %s: %s", session.id, e)
        with session._lock:
            if drained:
                session.output_buffer += drained
                if len(session.output_buffer) > session.max_output_chars:
                    session.output_buffer = session.output_buffer[-session.max_output_chars :]
            session.exited = True
            session.exit_code = rc
        logger.info(
            "Reconciled session %s: direct child exited with code %s "
            "but reader was still blocked (orphaned pipe). Flipped to exited.",
            session.id,
            rc,
        )
        self._move_to_finished(session)

    def poll(self, session_id: str, cancel_token: Any = None) -> dict:
        """查询后台进程的状态与最新输出。"""
        if cancel_token is not None and getattr(cancel_token, "is_set", lambda: False)():
            return {"status": "interrupted", "note": "Caller cancelled before reading"}
        session = self.get(session_id)
        if session is None:
            return {"status": "not_found", "error": f"No process with ID {session_id}"}
        # 先对账真实子进程状态, 防止孤儿 pipe 让 reader 卡死。
        self._reconcile_local_exit(session)
        # ``output_preview`` 受 ``registry.get_max_result_size()`` 兜底: 一行 2KB 的日志乘以多行也可能远超上限。
        max_chars = registry.get_max_result_size()
        with session._lock:
            preview_raw = clean_output(session.output_buffer[-1000:]) if session.output_buffer else ""
        preview_raw = redact_sensitive_text(preview_raw) if preview_raw else ""
        truncated = len(preview_raw) > max_chars
        output_preview = preview_raw[:max_chars] if truncated else preview_raw
        result = {
            "session_id": session.id,
            "command": session.command,
            "status": "exited" if session.exited else "running",
            "pid": session.pid,
            "uptime_seconds": int(time.time() - session.started_at),
            "output_preview": output_preview,
        }
        if truncated:
            result["output_truncated"] = True
        if session.exited:
            result["exit_code"] = session.exit_code
            self._completion_consumed.add(session_id)
        if session.detached:
            result["detached"] = True
            result["note"] = "Process recovered after restart -- output history unavailable"
        return result

    def read_log(self, session_id: str, offset: int = 0, limit: int = 200) -> dict:
        """读取完整输出日志, 支持按行分页(默认返回末尾 N 行)。"""
        session = self.get(session_id)
        if session is None:
            return {"status": "not_found", "error": f"No process with ID {session_id}"}
        with session._lock:
            full_output = clean_output(session.output_buffer)
        lines = full_output.splitlines()
        total_lines = len(lines)
        selected = lines[-limit:] if offset == 0 and limit > 0 else lines[offset : offset + limit]
        joined = redact_sensitive_text("\n".join(selected))
        # 末尾再按 ``registry.get_max_result_size()`` 兜底截断: 一行 2KB 的 JSON 日志乘以 200 行可能远超上限。
        max_chars = registry.get_max_result_size()
        truncated = len(joined) > max_chars
        if truncated:
            joined = joined[:max_chars]
        result = {
            "session_id": session.id,
            "status": "exited" if session.exited else "running",
            "output": joined,
            "total_lines": total_lines,
            "showing": f"{len(selected)} lines",
        }
        if truncated:
            result["truncated"] = True
            result["hint"] = f"Output exceeded {max_chars} chars; truncated. Use offset to page through earlier lines."
        if session.exited:
            self._completion_consumed.add(session_id)
        return result

    def wait(self, session_id: str, timeout: int | None = None, cancel_token: Any = None) -> dict:
        """阻塞直到进程退出、超时或被取消; 超时上限用 config.terminal.timeout 防模型设了巨值。"""
        try:
            default_timeout = int(cfg_get(load_config(), "terminal", "timeout", default=180))
        except (ValueError, TypeError):
            default_timeout = 180
        max_timeout = default_timeout
        requested_timeout = timeout
        timeout_note = None
        if requested_timeout and requested_timeout > max_timeout:
            effective_timeout = max_timeout
            timeout_note = f"Requested wait of {requested_timeout}s was clamped to configured limit of {max_timeout}s"
        else:
            effective_timeout = requested_timeout or max_timeout
        session = self.get(session_id)
        if session is None:
            return {"status": "not_found", "error": f"No process with ID {session_id}"}
        deadline = time.monotonic() + effective_timeout
        while time.monotonic() < deadline:
            session = self._refresh_detached_session(session)
            # 对账真实子进程状态 — 防孤儿 child 已退出的情况。
            self._reconcile_local_exit(session)
            if session.exited:
                self._completion_consumed.add(session_id)
                result = {
                    "status": "exited",
                    "exit_code": session.exit_code,
                    "output": clean_output(session.output_buffer[-2000:]),
                }
                if timeout_note:
                    result["timeout_note"] = timeout_note
                return result
            if is_interrupted() or (cancel_token is not None and getattr(cancel_token, "is_set", lambda: False)()):
                result = {
                    "status": "interrupted",
                    "output": clean_output(session.output_buffer[-1000:]),
                    "note": "Caller cancelled the wait",
                }
                if timeout_note:
                    result["timeout_note"] = timeout_note
                return result
            time.sleep(1)
        result = {"status": "timeout", "output": clean_output(session.output_buffer[-1000:])}
        if timeout_note:
            result["timeout_note"] = timeout_note
        else:
            result["timeout_note"] = f"Waited {effective_timeout}s, process still running"
        return result

    def kill_process(self, session_id: str) -> dict:
        """终止一个后台进程(支持 PTY / 本地 Popen / 非本地 env / detached host PID 四种 session)。"""
        session = self.get(session_id)
        if session is None:
            return {"status": "not_found", "error": f"No process with ID {session_id}"}
        if session.exited:
            return {"status": "already_exited", "exit_code": session.exit_code}
        try:
            if session._pty:
                try:
                    session._pty.terminate(force=True)
                except Exception:
                    # PTY 自身 ``terminate(force=True)`` 抛异常时的降级路径:
                    # 委派 helper, 走 pgid 优先 + psutil 兜底, Windows 走 taskkill。
                    if session.pid:
                        try:
                            terminate_tree(session.pid, graceful_timeout=0.5, force_timeout=1.0)
                        except (ProcessLookupError, PermissionError, OSError):
                            with contextlib.suppress(OSError, ProcessLookupError, PermissionError):
                                os.kill(session.pid, signal.SIGTERM)
            elif session.process:
                # 本地进程 — 连同进程树一起杀。
                try:
                    terminate_tree(session.process, graceful_timeout=0.5, force_timeout=1.0)
                except (ProcessLookupError, PermissionError):
                    session.process.kill()
            elif session.env_ref and session.pid:
                # 非本地: 记录下来的 pid 是包壳 subshell($!), 真命令是它的子进程,
                # 因此连带 pkill -P 一同信号, 残留再用 SIGKILL 升级。
                qpid = shlex.quote(str(session.pid))
                session.env_ref.execute(f"pkill -TERM -P {qpid} 2>/dev/null; kill {qpid} 2>/dev/null", timeout=5)
                alive = session.env_ref.execute(
                    f"kill -0 {qpid} 2>/dev/null || pgrep -P {qpid} >/dev/null 2>&1",
                    timeout=5,
                )
                if alive.get("returncode", 1) == 0:
                    session.env_ref.execute(f"pkill -KILL -P {qpid} 2>/dev/null; kill -9 {qpid} 2>/dev/null", timeout=5)
            elif session.detached and session.pid_scope == "host" and session.pid:
                if not self._is_host_pid_alive(session.pid):
                    with session._lock:
                        session.exited = True
                        session.exit_code = None
                    self._move_to_finished(session)
                    return {"status": "already_exited", "exit_code": session.exit_code}
                self._terminate_host_pid(session.pid)
            else:
                return {
                    "status": "error",
                    "error": (
                        (
                            "Recovered process cannot be killed after restart because its "
                            "original runtime handle is no longer available"
                        ),
                    ),
                }
            # 只上报真实观察到的 exit code: taskkill /F 和沙箱里的 kill 都不映射到 SIGTERM 的 -15。
            proc = getattr(session, "process", None)
            with session._lock:
                session.exited = True
                session.exit_code = proc.returncode if proc is not None and proc.poll() is not None else None
            self._move_to_finished(session)
            self._write_checkpoint()
            return {"status": "killed", "session_id": session.id}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def write_stdin(self, session_id: str, data: str) -> dict:
        session = self.get(session_id)
        if session is None:
            return {"status": "not_found", "error": f"No process with ID {session_id}"}
        if session.exited:
            return {"status": "already_exited", "error": "Process has already finished"}
        chunk_size = 4096
        if hasattr(session, "_pty") and session._pty:
            try:
                pty_proc = session._pty
                deadline = time.monotonic() + 5.0
                if IS_WINDOWS:
                    str_data = str(data)
                    for i in range(0, len(str_data), chunk_size):
                        chunk = str_data[i : i + chunk_size]
                        retries = 2
                        while retries > 0:
                            if time.monotonic() > deadline:
                                raise PTYBufferFull("PTY write timed out due to buffer backpressure")
                            try:
                                pty_proc.write(chunk)
                                time.sleep(0.001)
                                break
                            except (OSError, ValueError) as exc:
                                retries -= 1
                                if retries == 0:
                                    raise PTYBufferFull(f"PTY buffer full: {exc}") from exc
                                time.sleep(0.01)
                else:
                    bytes_data = data.encode("utf-8") if isinstance(data, str) else bytes(data)
                    for i in range(0, len(bytes_data), chunk_size):
                        chunk = bytes_data[i : i + chunk_size]
                        retries = 3
                        while retries > 0:
                            if time.monotonic() > deadline:
                                raise PTYBufferFull("PTY write timed out due to buffer backpressure")
                            try:
                                pty_proc.write(chunk)
                                time.sleep(0.001)
                                break
                            except (BlockingIOError, OSError) as exc:
                                retries -= 1
                                if retries == 0:
                                    raise PTYBufferFull(f"PTY buffer full: {exc}") from exc
                                time.sleep(0.01)
                return {"status": "ok", "bytes_written": len(data)}
            except Exception as e:
                return {"status": "error", "error": str(e)}
        if not session.process or not session.process.stdin:
            return {"status": "error", "error": "Process stdin not available (non-local backend or stdin closed)"}
        try:
            stdin = session.process.stdin
            str_data = str(data)
            for i in range(0, len(str_data), chunk_size):
                chunk = str_data[i : i + chunk_size]
                stdin.write(chunk)
                stdin.flush()
                time.sleep(0.001)
            return {"status": "ok", "bytes_written": len(str_data)}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def submit_stdin(self, session_id: str, data: str = "") -> dict:
        """向运行中进程的 stdin 发送 data + 换行(等价于按一次 Enter)。"""
        return self.write_stdin(session_id, data + "\n")

    def close_stdin(self, session_id: str) -> dict:
        """关闭运行中进程的 stdin(发 EOF), 不杀进程。"""
        session = self.get(session_id)
        if session is None:
            return {"status": "not_found", "error": f"No process with ID {session_id}"}
        if session.exited:
            return {"status": "already_exited", "error": "Process has already finished"}
        if hasattr(session, "_pty") and session._pty:
            try:
                session._pty.sendeof()
                return {"status": "ok", "message": "EOF sent"}
            except Exception as e:
                return {"status": "error", "error": str(e)}
        if not session.process or not session.process.stdin:
            return {"status": "error", "error": "Process stdin not available (non-local backend or stdin closed)"}
        try:
            session.process.stdin.close()
            return {"status": "ok", "message": "stdin closed"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def count_running(self) -> int:
        """返回当前正在运行的后台进程数(读 ``_running`` 字典 O(1), 状态栏每帧轮询也安全)。"""
        try:
            return len(self._running)
        except Exception:
            return 0

    def list_sessions(self, task_id: str | None = None) -> list:
        """列出所有运行中和最近结束的后台进程(可按 task_id 过滤)。"""
        with self._lock:
            all_sessions = list(self._running.values()) + list(self._finished.values())
        all_sessions = [self._refresh_detached_session(s) for s in all_sessions]
        if task_id:
            all_sessions = [s for s in all_sessions if s.task_id == task_id]
        result = []
        for s in all_sessions:
            # 先对完整字符串脱敏再切片: 否则一个跨 200 字符边界的密钥会被切在 token 中间,
            # 脱敏正则匹配不到前缀片段就漏过。
            entry = {
                "session_id": s.id,
                "command": clean_output(s.command)[:200],
                "cwd": clean_output(s.cwd) if s.cwd else s.cwd,
                "pid": s.pid,
                "started_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(s.started_at)),
                "uptime_seconds": int(time.time() - s.started_at),
                "status": "exited" if s.exited else "running",
                "output_preview": clean_output(s.output_buffer)[-200:] if s.output_buffer else "",
            }
            if s.exited:
                entry["exit_code"] = s.exit_code
            if s.detached:
                entry["detached"] = True
            result.append(entry)
        return result

    # ----- Session/Task Queries (for gateway integration) -----
    def _has_active(self, key: str, value: str) -> bool:
        with self._lock:
            sessions = list(self._running.values())
        for session in sessions:
            self._refresh_detached_session(session)
        with self._lock:
            return any(getattr(s, key) == value and not s.exited for s in self._running.values())

    def has_active_processes(self, task_id: str) -> bool:
        """判断 task_id 下是否还有运行中进程。"""
        return self._has_active("task_id", task_id)

    def has_active_for_session(self, session_key: str) -> bool:
        """判断 gateway session_key 下是否还有运行中进程。"""
        return self._has_active("session_key", session_key)

    def kill_all(self, task_id: str | None = None) -> int:
        """杀掉全部运行中进程(可选 task_id 过滤), 返回成功杀掉的条数。"""
        with self._lock:
            targets = [s for s in self._running.values() if (task_id is None or s.task_id == task_id) and not s.exited]
        killed = 0
        for session in targets:
            result = self.kill_process(session.id)
            if result.get("status") in {"killed", "already_exited"}:
                killed += 1
        return killed

    # ----- Cleanup / Pruning -----
    def _prune_if_needed(self) -> None:
        """超过 MAX_PROCESSES 时淘汰最旧的已结束 session(调用方需持 ``_lock``)。"""
        now = time.time()
        expired = [sid for sid, s in self._finished.items() if (now - s.started_at) > FINISHED_TTL_SECONDS]
        for sid in expired:
            del self._finished[sid]
            self._completion_consumed.discard(sid)
        # 仍超限时按 started_at 顺序再淘汰最旧的。
        total = len(self._running) + len(self._finished)
        if total >= MAX_PROCESSES and self._finished:
            oldest_id = min(self._finished, key=lambda sid: self._finished[sid].started_at)
            del self._finished[oldest_id]
            self._completion_consumed.discard(oldest_id)
        # belt-and-suspenders: 清理不在任何已跟踪集合里的 completion 残留 id,
        # 防止模块生命周期内的_lookup 路径绕过上面 dict prune 时无限增长。
        tracked = self._running.keys() | self._finished.keys()
        stale = self._completion_consumed - tracked
        if stale:
            self._completion_consumed -= stale

    # ----- Checkpoint (crash recovery) -----
    def _write_checkpoint(self) -> None:
        """把运行中的进程元数据原子写入 checkpoint 文件(崩溃恢复用)。"""
        try:
            with self._lock:
                entries = []
                for s in self._running.values():
                    if not s.exited:
                        entries.append(
                            {
                                "session_id": s.id,
                                "command": s.command,
                                "pid": s.pid,
                                "pid_scope": s.pid_scope,
                                "cwd": s.cwd,
                                "started_at": s.started_at,
                                "task_id": s.task_id,
                                "session_key": s.session_key,
                                "watcher_platform": s.watcher_platform,
                                "watcher_chat_id": s.watcher_chat_id,
                                "watcher_user_id": s.watcher_user_id,
                                "watcher_user_name": s.watcher_user_name,
                                "watcher_thread_id": s.watcher_thread_id,
                                "watcher_message_id": s.watcher_message_id,
                                "watcher_interval": s.watcher_interval,
                                "notify_on_complete": s.notify_on_complete,
                                "watch_patterns": s.watch_patterns,
                            },
                        )
            atomic_replace(str(CHECKPOINT_PATH), json.dumps(entries))
        except Exception as e:
            logger.debug("Failed to write checkpoint file: %s", e, exc_info=True)

    def recover_from_checkpoint(self) -> int:
        """Gateway 启动时读取 checkpoint, 探测 PID, 把仍存活的恢复成 detached session; 返回恢复条数。"""
        if not CHECKPOINT_PATH.exists():
            return 0
        try:
            entries = json.loads(CHECKPOINT_PATH.read_text(encoding="utf-8"))
        except Exception:
            return 0
        recovered = 0
        for entry in entries:
            pid = entry.get("pid")
            if not pid:
                continue
            pid_scope = entry.get("pid_scope", "host")
            if pid_scope != "host":
                # 沙箱后端进程在 checkpoint 里只有沙箱内 PID, host 进程一旦重启 + 原 env handle 消失,
                # 这些 PID 就不再有意义, 直接跳过。
                logger.info(
                    "Skipping recovery for non-host process: %s (pid=%s, scope=%s)",
                    entry.get("command", "unknown")[:60],
                    pid,
                    pid_scope,
                )
                continue
            alive = self._is_host_pid_alive(pid)
            if alive:
                session = ProcessSession(
                    id=entry["session_id"],
                    command=entry.get("command", "unknown"),
                    task_id=entry.get("task_id", ""),
                    session_key=entry.get("session_key", ""),
                    pid=pid,
                    pid_scope=pid_scope,
                    cwd=entry.get("cwd"),
                    started_at=entry.get("started_at", time.time()),
                    detached=True,  # 没拿到原 pipe, 无法读输出, 但能报状态、能杀
                    watcher_platform=entry.get("watcher_platform", ""),
                    watcher_chat_id=entry.get("watcher_chat_id", ""),
                    watcher_user_id=entry.get("watcher_user_id", ""),
                    watcher_user_name=entry.get("watcher_user_name", ""),
                    watcher_thread_id=entry.get("watcher_thread_id", ""),
                    watcher_message_id=entry.get("watcher_message_id", ""),
                    watcher_interval=entry.get("watcher_interval", 0),
                    notify_on_complete=entry.get("notify_on_complete", False),
                    watch_patterns=entry.get("watch_patterns", []),
                )
                with self._lock:
                    self._running[session.id] = session
                recovered += 1
                logger.info("Recovered detached process: %s (pid=%d)", session.command[:60], pid)
                # 把 watcher 重新入队, gateway 续接通知。
                if session.watcher_interval > 0:
                    self.pending_watchers.append(
                        {
                            "session_id": session.id,
                            "check_interval": session.watcher_interval,
                            "session_key": session.session_key,
                            "platform": session.watcher_platform,
                            "chat_id": session.watcher_chat_id,
                            "user_id": session.watcher_user_id,
                            "user_name": session.watcher_user_name,
                            "thread_id": session.watcher_thread_id,
                            "message_id": session.watcher_message_id,
                            "notify_on_complete": session.notify_on_complete,
                        },
                    )
        self._write_checkpoint()
        return recovered


process_registry = ProcessRegistry()
register_active_process_checker(process_registry.has_active_processes)


def format_process_notification(evt: dict) -> "str | None":
    """把进程通知事件格式化成 ``[IMPORTANT: ...]`` 文本; 处理三类事件
    (notify_on_complete 完成 / watch 模式匹配 / watch 停用)。
    """
    evt_type = evt.get("type", "completion")
    _sid = evt.get("session_id", "unknown")

    _cmd = clean_output(evt.get("command", "unknown"))
    if evt_type == "watch_disabled":
        return f"[IMPORTANT: {evt.get('message', '')}]"
    if evt_type == "watch_match":
        _pat = evt.get("pattern", "?")
        _out = evt.get("output", "")
        _sup = evt.get("suppressed", 0)
        text = (
            f'[IMPORTANT: Background process {_sid} matched watch pattern "{_pat}".\n'
            f"Command: {_cmd}\nMatched output:\n{_out}"
        )
        if _sup:
            text += f"\n({_sup} earlier matches were suppressed by rate limit)"
        text += "]"
        return text
    _exit = evt.get("exit_code", "?")
    _out = evt.get("output", "")
    return f"[IMPORTANT: Background process {_sid} completed (exit code {_exit}).\nCommand: {_cmd}\nOutput:\n{_out}]"


PROCESS_SCHEMA = {
    "name": "process",
    "description": (
        "Manage background processes started with terminal(background=true). "
        "Actions: 'list' (show all), 'poll' (check status + new output), "
        "'log' (full output with pagination), 'wait' (block until done or timeout), "
        "'kill' (terminate), 'write' (send raw stdin data without newline), "
        "'submit' (send data + Enter, for answering prompts), 'close' (close stdin/send EOF)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "poll", "log", "wait", "kill", "write", "submit", "close"],
                "description": "Action to perform on background processes",
            },
            "session_id": {
                "type": "string",
                "description": (
                    "Process session ID (from terminal background output). Required for all actions except 'list'."
                ),
            },
            "data": {
                "type": "string",
                "description": "Text to send to process stdin (for 'write' and 'submit' actions)",
            },
            "timeout": {
                "type": "integer",
                "description": "Max seconds to block for 'wait' action. Returns partial output on timeout.",
                "minimum": 1,
            },
            "offset": {"type": "integer", "description": "Line offset for 'log' action (default: last 200 lines)"},
            "limit": {"type": "integer", "description": "Max lines to return for 'log' action", "minimum": 1},
        },
        "required": ["action"],
    },
}


def _coerce_int(value: Any, default: int, *, field: str) -> int:
    """LLM 可能把 ``offset`` / ``limit`` / ``timeout`` 发成字符串。强制 ``int`` 并返回错误信封字符串。"""
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be an integer, got {type(value).__name__}: {value!r}")


def _handle_process(args: dict[str, Any], **kw: Any) -> str:
    task_id = kw.get("task_id")
    cancel_token = kw.get("cancel_token")
    action = args.get("action", "")
    # Coerce to string — some models send session_id as an integer
    session_id = str(args.get("session_id", "")) if args.get("session_id") is not None else ""
    try:
        offset = _coerce_int(args.get("offset"), 0, field="offset")
        limit = _coerce_int(args.get("limit"), 200, field="limit")
        timeout = _coerce_int(args.get("timeout"), 0, field="timeout") if args.get("timeout") is not None else None
    except ValueError as e:
        return tool_error(str(e))
    match action:
        case "list":
            return json.dumps({"processes": process_registry.list_sessions(task_id=task_id)}, ensure_ascii=False)
        case "poll" | "log" | "wait" | "kill" | "write" | "submit" | "close" if not session_id:
            return tool_error(f"session_id is required for {action}")
        case "poll":
            return json.dumps(process_registry.poll(session_id, cancel_token=cancel_token), ensure_ascii=False)
        case "log":
            return json.dumps(process_registry.read_log(session_id, offset=offset, limit=limit), ensure_ascii=False)
        case "wait":
            return json.dumps(
                process_registry.wait(session_id, timeout=timeout, cancel_token=cancel_token),
                ensure_ascii=False,
            )
        case "kill":
            return json.dumps(process_registry.kill_process(session_id), ensure_ascii=False)
        case "write":
            return json.dumps(process_registry.write_stdin(session_id, str(args.get("data", ""))), ensure_ascii=False)
        case "submit":
            return json.dumps(process_registry.submit_stdin(session_id, str(args.get("data", ""))), ensure_ascii=False)
        case "close":
            return json.dumps(process_registry.close_stdin(session_id), ensure_ascii=False)
        case _:
            return tool_error(
                f"Unknown process action: {action}. Use: list, poll, log, wait, kill, write, submit, close",
            )


registry.register_tool("process", schema=PROCESS_SCHEMA)(_handle_process)
