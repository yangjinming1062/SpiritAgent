import json
import logging
import os
import re
import sys
import threading
import time
import traceback
from copy import deepcopy
from typing import Any

from envs import (
    active_environments,
    create_environment,
    creation_locks,
    creation_locks_lock,
    env_lock,
    get_env_config,
    last_activity,
    resolve_container_task_id,
    start_cleanup_thread,
    task_env_overrides,
)
from utils import cfg_get, clean_output, is_interrupted, load_config, redact_sensitive_text

from ..process import process_registry
from ..registry import registry

logger = logging.getLogger(__name__)

DEFAULT_FOREGROUND_MAX_TIMEOUT = 600

_EXIT_CODE_SPLIT_RE = re.compile(r"\s*(?:\|\||&&|[|;])\s*")
_SINGLE_QUOTE_RE = re.compile(r"'[^']*'")
_DOUBLE_QUOTE_RE = re.compile(r'"(?:[^"\\]|\\.)*"')
_BACKTICK_RE = re.compile(r"`[^`]*`")

_cfg = load_config()
FOREGROUND_MAX_TIMEOUT = cfg_get(_cfg, "terminal", "max_foreground_timeout", default=DEFAULT_FOREGROUND_MAX_TIMEOUT)


_WORKDIR_SAFE_RE = re.compile(r"^[A-Za-z0-9/\\:_\-.~ +@=,]+$")


def _validate_workdir(workdir: str) -> str | None:
    if not workdir:
        return None
    if _WORKDIR_SAFE_RE.match(workdir):
        return None
    for ch in workdir:
        if not _WORKDIR_SAFE_RE.match(ch):
            return (
                f"Blocked: workdir contains disallowed character {ch!r}. "
                "Use a simple filesystem path without shell metacharacters."
            )
    return "Blocked: workdir contains disallowed characters."


def _safe_command_preview(command: Any, limit: int = 200) -> str:
    if command is None:
        return "<None>"
    if isinstance(command, str):
        return command[:limit]
    try:
        return repr(command)[:limit]
    except Exception:
        return f"<{type(command).__name__}>"


WINDOWS_TOOL_DESCRIPTION = """Execute shell commands on the user's Windows host through Git Bash.
This is the user's desktop machine, not a virtual machine or Linux container. The host filesystem
persists between calls.
Paths may use Windows absolute form (C:\\Users\\name or C:/Users/name) or Git Bash MSYS form
(/c/Users/name).
Use Bash syntax for chaining, quoting, and pipes, but invoke Windows executables and CLI tools directly.
Use winget, pip, npm, or cargo for packages. Do not use apt, apt-get, yum, dnf, pacman, systemctl, or sudo.
"""

MACOS_TOOL_DESCRIPTION = """Execute shell commands on the user's macOS (Darwin) host terminal through its
Bash-compatible shell.
This is the user's desktop Mac, not a virtual machine or Linux container. The host filesystem persists between calls.
Paths use standard POSIX form (/Users/name).
macOS provides BSD command-line tools and native utilities such as open, pbcopy, and sw_vers;
note BSD differences such as sed -i ''.
Use brew, pip, npm, or cargo for packages. Do not use apt, apt-get, yum, dnf, pacman, or systemctl.
The non-interactive shell cannot answer sudo password prompts; avoid sudo.
"""

REMOTE_TOOL_DESCRIPTION = """Execute shell commands on the configured remote host through its Bash-compatible shell.
The remote platform, tools, package manager, and filesystem depend on that host.
"""

TERMINAL_COMMON_DESCRIPTION = """Do NOT use cat/head/tail to read files — use read_file instead.
Do NOT use grep/rg/find to search — use search_files instead.
Do NOT use ls to list directories — use search_files(target='files') instead.
Do NOT use sed/awk to edit files — use patch instead.
Do NOT use echo/cat heredoc to create files — use write_file instead.
Reserve terminal for: builds, installs, git, processes, scripts, network, package managers, and anything
that needs a shell.

Foreground (default): Commands return INSTANTLY when done, even if the timeout is high.
Set timeout=300 for long builds/scripts — you'll still get the result in seconds if it's fast.
Prefer foreground for short commands.
Background: Set background=true to get a session_id. Almost always pair with notify_on_complete=true —
bg without notify runs SILENTLY and you have no way to learn it finished short of calling
process(action='poll') yourself. Two legitimate uses:
  (1) Long-lived processes that never exit (servers, watchers, daemons) — silent is correct,
      there's no exit to notify on.
  (2) Long-running bounded tasks (tests, builds, deploys, CI pollers, batch jobs) — MUST set
      notify_on_complete=true. Without it you'll either forget to poll or sit blocked waiting
      for the user to surface the result.
For servers/watchers, do NOT use shell-level background wrappers (nohup/disown/setsid/trailing '&')
in foreground mode. Use background=true so SpiritAgent can track lifecycle and output.
After starting a server, verify readiness with a health check or log signal, then run tests in a
separate terminal() call. Avoid blind sleep loops.
Use process(action="poll") for progress checks, process(action="wait") to block until done.
Working directory: Use 'workdir' for per-command cwd.
PTY mode: Set pty=true for interactive CLI tools (Codex, Claude Code, Python REPL).

Do NOT use vim/nano/interactive tools without pty=true — they hang without a pseudo-terminal.
Pipe git output to cat if it might page.
"""


def _normalize_terminal_platform(platform_name: str | None = None) -> str:
    normalized = (platform_name or sys.platform).lower()
    if normalized in {"win32", "windows", "cygwin", "msys"}:
        return "win32"
    if normalized in {"darwin", "macos"}:
        return "darwin"
    raise ValueError(
        f"Unsupported terminal host platform: {platform_name or sys.platform!r}. Use win32 or darwin.",
    )


def build_terminal_tool_description(platform_name: str | None = None, env_type: str = "local") -> str:
    if env_type == "ssh":
        backend_description = REMOTE_TOOL_DESCRIPTION
    else:
        platform = _normalize_terminal_platform(platform_name)
        backend_description = WINDOWS_TOOL_DESCRIPTION if platform == "win32" else MACOS_TOOL_DESCRIPTION
    return backend_description + "\n\n" + TERMINAL_COMMON_DESCRIPTION


def _interpret_exit_code(command: str, exit_code: int) -> str | None:
    if exit_code == 0:
        return None
    segments = _EXIT_CODE_SPLIT_RE.split(command)
    last_segment = (segments[-1] if segments else command).strip()
    words = last_segment.split()
    base_cmd = ""
    for w in words:
        if "=" in w and not w.startswith("-"):
            continue
        base_cmd = w.split("/")[-1]
        break
    if not base_cmd:
        return None
    semantics: dict[str, dict[int, str]] = {
        "grep": {1: "No matches found (not an error)"},
        "egrep": {1: "No matches found (not an error)"},
        "fgrep": {1: "No matches found (not an error)"},
        "rg": {1: "No matches found (not an error)"},
        "ag": {1: "No matches found (not an error)"},
        "ack": {1: "No matches found (not an error)"},
        "diff": {1: "Files differ (expected, not an error)"},
        "colordiff": {1: "Files differ (expected, not an error)"},
        "find": {1: "Some directories were inaccessible (partial results may still be valid)"},
        "test": {1: "Condition evaluated to false (expected, not an error)"},
        "[": {1: "Condition evaluated to false (expected, not an error)"},
        "curl": {
            6: "Could not resolve host",
            7: "Failed to connect to host",
            22: "HTTP response code indicated error (e.g. 404, 500)",
            28: "Operation timed out",
        },
        "git": {1: "Non-zero exit (often normal — e.g. 'git diff' returns 1 when files differ)"},
    }
    cmd_semantics = semantics.get(base_cmd)
    if cmd_semantics and exit_code in cmd_semantics:
        return cmd_semantics[exit_code]
    return None


def _command_requires_pipe_stdin(command: str) -> bool:
    normalized = " ".join(command.lower().split())
    return normalized.startswith("gh auth login") and "--with-token" in normalized


_SHELL_LEVEL_BACKGROUND_RE = re.compile(
    r"(?:^|[;&|]\s*|&&\s*|\|\|\s*|\$\(\s*)(?:nohup|disown|setsid)\b",
    re.IGNORECASE | re.MULTILINE,
)

_INLINE_BACKGROUND_AMP_RE = re.compile(r"\s&\s")

_TRAILING_BACKGROUND_AMP_RE = re.compile(r"\s&\s*(?:#.*)?$")


def _strip_quotes(command: str) -> str:
    result = _SINGLE_QUOTE_RE.sub("''", command)
    result = _DOUBLE_QUOTE_RE.sub('""', result)
    result = _BACKTICK_RE.sub("``", result)
    return result


_LONG_LIVED_FOREGROUND_PATTERNS = (
    re.compile(r"\b(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?(?:dev|start|serve|watch)\b", re.IGNORECASE),
    re.compile(r"\bdocker\s+compose\s+up\b", re.IGNORECASE),
    re.compile(r"\bnext\s+dev\b", re.IGNORECASE),
    re.compile(r"\bvite(?:\s|$)", re.IGNORECASE),
    re.compile(r"\bnodemon\b", re.IGNORECASE),
    re.compile(r"\buvicorn\b", re.IGNORECASE),
    re.compile(r"\bgunicorn\b", re.IGNORECASE),
    re.compile(r"\bpython(?:3)?\s+-m\s+http\.server\b", re.IGNORECASE),
)


def _looks_like_help_or_version_command(command: str) -> bool:
    normalized = " ".join(command.lower().split())
    return (
        " --help" in normalized
        or normalized.endswith(" -h")
        or " --version" in normalized
        or normalized.endswith(" -v")
    )


def _foreground_background_guidance(command: str) -> str | None:
    if _looks_like_help_or_version_command(command):
        return None
    unquoted = _strip_quotes(command)
    if _SHELL_LEVEL_BACKGROUND_RE.search(unquoted):
        return (
            "Foreground command uses shell-level background wrappers (nohup/disown/setsid). "
            "Use terminal(background=true) so SpiritAgent can track the process, then run "
            "readiness checks and tests in separate commands."
        )
    if _INLINE_BACKGROUND_AMP_RE.search(unquoted) or _TRAILING_BACKGROUND_AMP_RE.search(unquoted):
        return (
            "Foreground command uses '&' backgrounding. Use terminal(background=true) for long-lived "
            "processes, then run health checks and tests in follow-up terminal calls."
        )
    for pattern in _LONG_LIVED_FOREGROUND_PATTERNS:
        if pattern.search(unquoted):
            return (
                "This foreground command appears to start a long-lived server/watch process. "
                "Run it with background=true, verify readiness (health endpoint/log signal), "
                "then execute tests in a separate command."
            )
    return None


_COMMAND_START_RE = r"(?:^|[;&|`]\s*|\$\(\s*|\(\s*)"
_ENV_ASSIGNMENT_PREFIX_RE = r"(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*"
_SUDO_PREFIX_RE = r"(?:sudo(?:\s+-[A-Za-z0-9_-]+|\s+--\S+)*\s+)?"
_LINUX_PACKAGE_COMMAND_RE = re.compile(
    _COMMAND_START_RE
    + _ENV_ASSIGNMENT_PREFIX_RE
    + _SUDO_PREFIX_RE
    + r"(?:[^;&|\s]*[/\\])?(?P<command>apt-get|aptitude|apt|yum|dnf|pacman|zypper|apk)\b",
    re.IGNORECASE | re.MULTILINE,
)
_LINUX_SERVICE_COMMAND_RE = re.compile(
    _COMMAND_START_RE + _ENV_ASSIGNMENT_PREFIX_RE + _SUDO_PREFIX_RE + r"(?:[^;&|\s]*[/\\])?systemctl\b",
    re.IGNORECASE | re.MULTILINE,
)


def _blocked_host_command_error(command: str, env_type: str, platform_name: str | None = None) -> str | None:
    if env_type != "local":
        return None
    platform = _normalize_terminal_platform(platform_name)
    unquoted = _strip_quotes(command)
    package_match = _LINUX_PACKAGE_COMMAND_RE.search(unquoted)
    if package_match:
        blocked = package_match.group("command").lower()
        alternatives = "winget, pip, npm, or cargo" if platform == "win32" else "brew, pip, npm, or cargo"
        host = "Windows host running Git Bash" if platform == "win32" else "macOS host"
        return f"On {host}, '{blocked}' is unavailable. Use {alternatives} instead of Linux package managers."
    if _LINUX_SERVICE_COMMAND_RE.search(unquoted):
        host = "Windows" if platform == "win32" else "macOS"
        service_guidance = (
            "On Windows, manage services with native service tooling when explicitly requested."
            if platform == "win32"
            else "On macOS, use launchd-aware tooling or open applications instead of systemctl."
        )
        return f"systemctl is unavailable on this {host} host. {service_guidance}"
    return None


def _resolve_notification_flag_conflict(
    *,
    notify_on_complete: bool,
    watch_patterns: list[str] | None,
    background: bool,
) -> tuple[list[str] | None, str]:
    if background and notify_on_complete and watch_patterns:
        note = (
            "watch_patterns ignored because notify_on_complete=True; "
            "these two flags produce duplicate notifications when combined"
        )
        return None, note
    return watch_patterns, ""


def _resolve_command_cwd(*, workdir: str | None, env: Any, default_cwd: str) -> str:
    if workdir:
        return workdir
    live_cwd = getattr(env, "cwd", None)
    if isinstance(live_cwd, str) and live_cwd.strip():
        return live_cwd
    return default_cwd


def terminal_tool(
    command: str,
    background: bool = False,
    timeout: int | None = None,
    task_id: str | None = None,
    workdir: str | None = None,
    pty: bool = False,
    notify_on_complete: bool = False,
    watch_patterns: list[str] | None = None,
    cancel_token: Any = None,
) -> str:
    """在对应 task 的终端环境中执行单条 shell 命令——前台/后台互斥分支，复用 / 必要时懒创建环境。

    ``cancel_token``: 调用方注入的一次性取消令牌；工具函数在执行 / 等待 / 重试的关键阻塞点
    调用 ``is_interrupted()`` 兜底（基于 ``ContextVar`` 的 thread-id 中断位），覆盖 ``set_local_interrupt``
    之外的本地线程取消触发。
    """
    try:
        if not isinstance(command, str):
            logger.warning("Rejected invalid terminal command value: %s", type(command).__name__)
            return json.dumps(
                {
                    "output": "",
                    "exit_code": -1,
                    "error": f"Invalid command: expected string, got {type(command).__name__}",
                    "status": "error",
                },
                ensure_ascii=False,
            )
        config = get_env_config()
        env_type = config["env_type"]
        if blocked_host_error := _blocked_host_command_error(command, env_type):
            return json.dumps(
                {"output": "", "exit_code": -1, "error": blocked_host_error, "status": "blocked"},
                ensure_ascii=False,
            )
        effective_task_id = resolve_container_task_id(task_id)
        overrides = (task_env_overrides.get(task_id) if task_id else None) or task_env_overrides.get(
            effective_task_id,
            {},
        )
        cwd = overrides.get("cwd") or config["cwd"]
        default_timeout = config["timeout"]
        effective_timeout = timeout if timeout is not None else default_timeout
        if not background and timeout and timeout > FOREGROUND_MAX_TIMEOUT:
            return json.dumps(
                {
                    "error": (
                        f"Foreground timeout {timeout}s exceeds the maximum of "
                        f"{FOREGROUND_MAX_TIMEOUT}s. Use background=true with "
                        f"notify_on_complete=true for long-running commands."
                    ),
                },
                ensure_ascii=False,
            )
        if not background:
            guidance = _foreground_background_guidance(command)
            if guidance:
                return json.dumps(
                    {"output": "", "exit_code": -1, "error": guidance, "status": "error"},
                    ensure_ascii=False,
                )
        start_cleanup_thread()
        with env_lock:
            _existing_key = (
                effective_task_id
                if effective_task_id in active_environments
                else (task_id if task_id and task_id in active_environments else None)
            )
            if _existing_key is not None:
                last_activity[_existing_key] = time.time()
                env = active_environments[_existing_key]
                needs_creation = False
            else:
                needs_creation = True
        if needs_creation:
            with creation_locks_lock:
                if effective_task_id not in creation_locks:
                    creation_locks[effective_task_id] = threading.Lock()
                task_lock = creation_locks[effective_task_id]
            with task_lock:
                with env_lock:
                    _existing_key = (
                        effective_task_id
                        if effective_task_id in active_environments
                        else (task_id if task_id and task_id in active_environments else None)
                    )
                    if _existing_key is not None:
                        last_activity[_existing_key] = time.time()
                        env = active_environments[_existing_key]
                        needs_creation = False
                if needs_creation:
                    logger.info("Creating new %s environment for task %s...", env_type, effective_task_id[:8])
                    try:
                        ssh_config = None
                        if env_type == "ssh":
                            ssh_config = {
                                "host": config.get("ssh_host", ""),
                                "user": config.get("ssh_user", ""),
                                "port": config.get("ssh_port", 22),
                                "key": config.get("ssh_key", ""),
                                "password": config.get("ssh_password", ""),
                                "persistent": config.get("ssh_persistent", False),
                            }
                        local_config = None
                        if env_type == "local":
                            local_config = {"persistent": config.get("local_persistent", False)}
                        new_env = create_environment(
                            env_type=env_type,
                            cwd=cwd,
                            timeout=effective_timeout,
                            ssh_config=ssh_config,
                            local_config=local_config,
                            task_id=effective_task_id,
                        )
                    except ImportError as e:
                        return json.dumps(
                            {
                                "output": "",
                                "exit_code": -1,
                                "error": f"Terminal tool disabled: environment creation failed ({e})",
                                "status": "disabled",
                            },
                            ensure_ascii=False,
                        )
                    with env_lock:
                        active_environments[effective_task_id] = new_env
                        last_activity[effective_task_id] = time.time()
                        env = new_env
                    logger.info("%s environment ready for task %s", env_type, effective_task_id[:8])
        if workdir:
            workdir_error = _validate_workdir(workdir)
            if workdir_error:
                logger.warning(
                    "Blocked dangerous workdir: %s (command: %s)",
                    workdir[:200],
                    _safe_command_preview(command),
                )
                return json.dumps(
                    {"output": "", "exit_code": -1, "error": workdir_error, "status": "blocked"},
                    ensure_ascii=False,
                )
        pty_disabled_reason = None
        effective_pty = pty
        if pty and _command_requires_pipe_stdin(command):
            effective_pty = False
            pty_disabled_reason = (
                "PTY disabled for this command because it expects piped stdin/EOF "
                "(for example gh auth login --with-token). For local background "
                "processes, call process(action='close') after writing so it receives "
                "EOF."
            )
        if background:
            session_key = ""
            effective_cwd = _resolve_command_cwd(workdir=workdir, env=env, default_cwd=cwd)
            try:
                if env_type == "local":
                    proc_session = process_registry.spawn_local(
                        command=command,
                        cwd=effective_cwd,
                        task_id=effective_task_id,
                        session_key=session_key,
                        env_vars=env.env if hasattr(env, "env") else None,
                        use_pty=effective_pty,
                    )
                else:
                    proc_session = process_registry.spawn_via_env(
                        env=env,
                        command=command,
                        cwd=effective_cwd,
                        task_id=effective_task_id,
                        session_key=session_key,
                    )
                result_data = {
                    "output": "Background process started",
                    "session_id": proc_session.id,
                    "pid": proc_session.pid,
                    "exit_code": 0,
                    "error": None,
                }
                if pty_disabled_reason:
                    result_data["pty_note"] = pty_disabled_reason
                if background and not notify_on_complete and not watch_patterns:
                    result_data["hint"] = (
                        "background=true without notify_on_complete=true means "
                        "this process runs SILENTLY — you will not be told when "
                        "it exits. If this is a bounded task (test suite, build, "
                        "CI poller, deploy, anything with a defined end), you "
                        "almost certainly wanted notify_on_complete=true so the "
                        "system pings you on exit. Re-launch with "
                        "notify_on_complete=true, or call process(action='poll') "
                        "/ process(action='wait') yourself to learn the outcome. "
                        "Only ignore this hint for genuine long-lived processes "
                        "that never exit (servers, watchers, daemons)."
                    )
                if background and command:
                    _gh = "gh pr view" in command or "gh pr checks" in command
                    _has_jq = " jq " in command or "| jq" in command or "$(jq" in command
                    _bad_shape = "statusCheckRollup" in command or (_gh and _has_jq)
                    if _bad_shape:
                        existing = result_data.get("hint", "")
                        canonical_hint = (
                            "This looks like a homebrewed CI poller built from "
                            "`gh pr view --json statusCheckRollup` and/or "
                            "`gh pr checks | jq`. That shape has burned us "
                            "repeatedly in spiritagent-agent dev work (PRs #31329, "
                            "#31448, #31695, #31709, #31745, #32264, #33131) — "
                            "stdout buffering kills output capture, jq null-key "
                            "edge cases silently exit the loop, conclusion-vs-"
                            "status field confusion exits early with bogus "
                            "all-green verdicts, TTY-only summary banners "
                            "never appear when piped. Use the canonical "
                            "snippets in the green-ci-policy skill instead: "
                            "the exit-code-driven `gh pr checks $PR >/dev/null` "
                            "(rc 0 = green, 8 = pending, else fail) for "
                            "exit-on-first-fail behavior, or the column-2 "
                            "awk-on-tabs poller "
                            '(`awk -F"\\t" "$2==\\"pending\\""`) for '
                            "sharded matrices. Load skill_view("
                            "name='github/spiritagent-agent-dev', "
                            "file_path='references/green-ci-policy.md') for "
                            "the verbatim snippets. If you must roll a custom "
                            "loop with rich structured output, write each tick "
                            "to a known file (`tee -a /tmp/ci.log`) and rely "
                            "on `process(action='log')` to read THAT file — "
                            "do not rely on background-process stdout capture "
                            "for line-buffered shell loops."
                        )
                        result_data["hint"] = existing + "\n\n" + canonical_hint if existing else canonical_hint
                if background and (notify_on_complete or watch_patterns):
                    _gw_platform = os.environ.get("SPIRITAGENT_SESSION_PLATFORM", "")
                    if _gw_platform:
                        _gw_chat_id = os.environ.get("SPIRITAGENT_SESSION_CHAT_ID", "")
                        _gw_thread_id = os.environ.get("SPIRITAGENT_SESSION_THREAD_ID", "")
                        _gw_user_id = os.environ.get("SPIRITAGENT_SESSION_USER_ID", "")
                        _gw_user_name = os.environ.get("SPIRITAGENT_SESSION_USER_NAME", "")
                        _gw_message_id = os.environ.get("SPIRITAGENT_SESSION_MESSAGE_ID", "")
                        proc_session.watcher_platform = _gw_platform
                        proc_session.watcher_chat_id = _gw_chat_id
                        proc_session.watcher_user_id = _gw_user_id
                        proc_session.watcher_user_name = _gw_user_name
                        proc_session.watcher_thread_id = _gw_thread_id
                        proc_session.watcher_message_id = _gw_message_id
                watch_patterns, conflict_note = _resolve_notification_flag_conflict(
                    notify_on_complete=bool(notify_on_complete),
                    watch_patterns=watch_patterns,
                    background=bool(background),
                )
                if conflict_note:
                    logger.warning("background proc %s: %s", proc_session.id, conflict_note)
                    result_data["watch_patterns_ignored"] = conflict_note
                if notify_on_complete and background:
                    proc_session.notify_on_complete = True
                    result_data["notify_on_complete"] = True
                    if proc_session.watcher_platform:
                        proc_session.watcher_interval = 5
                        process_registry.pending_watchers.append(
                            {
                                "session_id": proc_session.id,
                                "check_interval": 5,
                                "session_key": session_key,
                                "platform": proc_session.watcher_platform,
                                "chat_id": proc_session.watcher_chat_id,
                                "user_id": proc_session.watcher_user_id,
                                "user_name": proc_session.watcher_user_name,
                                "thread_id": proc_session.watcher_thread_id,
                                "message_id": proc_session.watcher_message_id,
                                "notify_on_complete": True,
                            },
                        )
                if watch_patterns and background:
                    proc_session.watch_patterns = list(watch_patterns)
                    result_data["watch_patterns"] = proc_session.watch_patterns
                return json.dumps(result_data, ensure_ascii=False)
            except Exception as e:
                return json.dumps(
                    {"output": "", "exit_code": -1, "error": f"Failed to start background process: {e!s}"},
                    ensure_ascii=False,
                )
        else:
            max_retries = 3
            retry_count = 0
            result = None
            while retry_count <= max_retries:
                # ``cancel_token`` 触发 / 本线程被 ``set_localinterrupt`` 时立刻退出, 不再等下一次 sleep。
                if (cancel_token is not None and getattr(cancel_token, "is_set", lambda: False)()) or is_interrupted():
                    return json.dumps(
                        {"output": "", "exit_code": 130, "error": "Command interrupted", "status": "cancelled"},
                        ensure_ascii=False,
                    )
                try:
                    execute_kwargs = {
                        "timeout": effective_timeout,
                        "cwd": _resolve_command_cwd(workdir=workdir, env=env, default_cwd=cwd),
                    }
                    result = env.execute(command, **execute_kwargs)
                except Exception as e:
                    error_str = str(e).lower()
                    if "timeout" in error_str:
                        return json.dumps(
                            {
                                "output": "",
                                "exit_code": 124,
                                "error": f"Command timed out after {effective_timeout} seconds",
                            },
                            ensure_ascii=False,
                        )
                    if retry_count < max_retries:
                        retry_count += 1
                        wait_time = 2**retry_count
                        if (
                            cancel_token is not None and getattr(cancel_token, "is_set", lambda: False)()
                        ) or is_interrupted():
                            return json.dumps(
                                {
                                    "output": "",
                                    "exit_code": 130,
                                    "error": "Command interrupted during retry wait",
                                    "status": "cancelled",
                                },
                                ensure_ascii=False,
                            )
                        logger.warning(
                            "Execution error, retrying in %ds (attempt %d/%d) "
                            "- Command: %s - Error: %s: %s - Task: %s, Backend: %s",
                            wait_time,
                            retry_count,
                            max_retries,
                            _safe_command_preview(command),
                            type(e).__name__,
                            e,
                            effective_task_id,
                            env_type,
                        )
                        time.sleep(wait_time)
                        continue
                    logger.error(
                        "Execution failed after %d retries - Command: %s - Error: %s: %s - Task: %s, Backend: %s",
                        max_retries,
                        _safe_command_preview(command),
                        type(e).__name__,
                        e,
                        effective_task_id,
                        env_type,
                    )
                    return json.dumps(
                        {
                            "output": "",
                            "exit_code": -1,
                            "error": f"Command execution failed: {type(e).__name__}: {e!s}",
                        },
                        ensure_ascii=False,
                    )
                break
            output = result.get("output", "")
            returncode = result.get("returncode", 0)
            # 字符预算（registry 的结果大小上限）：混用字节上限会让 CJK 输出突破 LLM 载荷上限。
            max_output_chars = registry.get_max_result_size()
            if len(output) > max_output_chars:
                head_chars = int(max_output_chars * 0.4)
                tail_chars = max_output_chars - head_chars
                omitted = len(output) - head_chars - tail_chars
                truncated_notice = (
                    f"\n\n... [OUTPUT TRUNCATED - {omitted} chars omitted out of {len(output)} total] ...\n\n"
                )
                output = output[:head_chars] + truncated_notice + output[-tail_chars:]
            output = clean_output(output.strip()) if output else ""
            # 再过一道 ``redact_sensitive_text``: 终端输出常常含
            # ``curl -H "Authorization: Bearer ..."`` / ``env`` / ``cat ~/.aws/credentials`` 等
            # 敏感凭据; ``clean_output`` 只剥 ANSI, 必须额外走脱敏, 否则模型会拿到明文 token。
            output = redact_sensitive_text(output) if output else ""
            exit_note = _interpret_exit_code(command, returncode)
            error_msg = result.get("error")
            if not error_msg and returncode != 0:
                error_msg = f"Process exited with non-zero code {returncode}"
            result_dict = {"output": output, "exit_code": returncode, "error": error_msg}
            if exit_note:
                result_dict["exit_code_meaning"] = exit_note
            return json.dumps(result_dict, ensure_ascii=False)
    except Exception as e:
        # 不要把 ``traceback.format_exc()`` 回灌给模型: 含 runner 内部绝对路径 / 行号 / 用户代码路径,
        # 远超出 LLM 该看的范围。完整 traceback 留 ``logger.error`` 服务端诊断。
        tb_str = traceback.format_exc()
        logger.error("terminal_tool exception:\n%s", tb_str)
        return json.dumps(
            {
                "output": "",
                "exit_code": -1,
                "error": f"Failed to execute command: {type(e).__name__}: {e}",
                "status": "error",
            },
            ensure_ascii=False,
        )


_TERMINAL_SCHEMA_TEMPLATE = {
    "name": "terminal",
    "description": "",
    "parameters": {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": ""},
            "background": {
                "type": "boolean",
                "description": (
                    "Run the command in the background. Almost always pair with "
                    "notify_on_complete=true — without it, the process runs silently and you'll "
                    "have no way to learn it finished short of calling process(action='poll') "
                    "yourself (easy to forget, leading to silent blindness on long jobs). Two "
                    "legitimate patterns: (1) Long-lived processes that never exit "
                    "(servers, watchers, daemons) — these stay silent because there's no exit "
                    "to notify on. (2) Long-running bounded tasks (tests, builds, deploys, "
                    "CI pollers, batch jobs) — these MUST set notify_on_complete=true. For "
                    "short commands, prefer foreground with a generous timeout instead."
                ),
                "default": False,
            },
            "timeout": {
                "type": "integer",
                "description": (
                    "Max seconds to wait (default: 180). Returns INSTANTLY when command finishes — "
                    "set high for long tasks, you won't wait unnecessarily. Foreground timeouts "
                    "above the configured cap (600 by default, overridable via the "
                    "TERMINAL_MAX_FOREGROUND_TIMEOUT env var) are rejected; use background=true "
                    "for longer commands."
                ),
                "minimum": 1,
            },
            "workdir": {
                "type": "string",
                "description": (
                    "Working directory for this command (absolute path). Defaults to the session working directory."
                ),
            },
            "pty": {
                "type": "boolean",
                "description": (
                    "Run in pseudo-terminal (PTY) mode for interactive CLI tools like Codex, "
                    "Claude Code, or Python REPL. Only works with local and SSH backends. "
                    "Default: false."
                ),
                "default": False,
            },
            "notify_on_complete": {
                "type": "boolean",
                "description": (
                    "When true (and background=true), you'll be automatically notified exactly "
                    "once when the process finishes. **This is the right choice for almost every "
                    "long-running task** — tests, builds, deployments, multi-item batch jobs, "
                    "anything that takes over a minute and has a defined end. Use this and keep "
                    "working on other things; the system notifies you on exit. "
                    "MUTUALLY EXCLUSIVE with watch_patterns — when both are set, "
                    "watch_patterns is dropped."
                ),
                "default": False,
            },
            "watch_patterns": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Strings to watch for in background process output. "
                    "HARD RATE LIMIT: at most 1 notification per 15 seconds per process — "
                    "matches arriving inside the cooldown are dropped. After 3 consecutive "
                    "15-second windows with dropped matches, watch_patterns is automatically "
                    "disabled for that process and promoted to notify_on_complete behavior "
                    "(one notification on exit, no more mid-process spam). USE ONLY for "
                    "truly rare, one-shot mid-process signals on LONG-LIVED processes that "
                    "will never exit on their own — e.g. ['Application startup complete'] on "
                    "a server so you know when to hit its endpoint, or ['migration done'] on "
                    "a daemon. DO NOT use for: (1) end-of-run markers like 'DONE'/'PASS' — "
                    "use notify_on_complete instead; (2) error patterns like 'ERROR'/"
                    "'Traceback' in loops or multi-item batch jobs — they fire on every "
                    "iteration and you'll hit the strike limit fast; (3) anything you'd ever "
                    "combine with notify_on_complete. When in doubt, choose "
                    "notify_on_complete. MUTUALLY EXCLUSIVE with notify_on_complete — "
                    "set one, not both."
                ),
            },
        },
        "required": ["command"],
    },
}


def build_terminal_schema(platform_name: str | None = None, env_type: str = "local") -> dict[str, Any]:
    if env_type == "ssh":
        command_description = "The shell command to execute on the configured remote host."
    else:
        platform = _normalize_terminal_platform(platform_name)
        command_description = (
            "The shell command to execute on the user's Windows host via Git Bash."
            if platform == "win32"
            else "The shell command to execute on the user's macOS host terminal."
        )

    schema = deepcopy(_TERMINAL_SCHEMA_TEMPLATE)
    schema["description"] = build_terminal_tool_description(platform_name, env_type)
    schema["parameters"]["properties"]["command"]["description"] = command_description
    return schema


def _handle_terminal(args: dict[str, Any], **kw: Any) -> str:
    return terminal_tool(
        command=args.get("command"),
        background=args.get("background", False),
        timeout=args.get("timeout"),
        task_id=kw.get("task_id"),
        workdir=args.get("workdir"),
        pty=args.get("pty", False),
        notify_on_complete=args.get("notify_on_complete", False),
        watch_patterns=args.get("watch_patterns"),
        cancel_token=kw.get("cancel_token"),
    )


registry.register_tool("terminal", schema=build_terminal_schema(env_type=get_env_config()["env_type"]))(
    _handle_terminal,
)
