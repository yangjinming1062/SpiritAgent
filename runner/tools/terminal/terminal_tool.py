import json
import logging
import re
import sys
from copy import deepcopy
from typing import Any

from envs import BaseEnvironment, EnvironmentBusyError, get_env_config, resolve_container_task_id, use_environment
from utils import cfg_get, clean_output, is_interrupted, load_config

from ..process import process_registry
from ..registry import registry

logger = logging.getLogger(__name__)

DEFAULT_FOREGROUND_MAX_TIMEOUT = 600

_EXIT_CODE_SPLIT_RE = re.compile(r"\s*(?:\|\||&&|[|;])\s*")
_SINGLE_QUOTE_RE = re.compile(r"'[^']*'")
_DOUBLE_QUOTE_RE = re.compile(r'"(?:[^"\\]|\\.)*"')
_BACKTICK_RE = re.compile(r"`[^`]*`")

_WORKDIR_SAFE_RE = re.compile(r"^[A-Za-z0-9/\\:_\-.~ +@=,]+$")


def _foreground_max_timeout() -> int:
    # 调用时读取：导入早于配置推送。
    try:
        return int(cfg_get(load_config(), "terminal", "max_foreground_timeout", default=DEFAULT_FOREGROUND_MAX_TIMEOUT))
    except (TypeError, ValueError):
        return DEFAULT_FOREGROUND_MAX_TIMEOUT


def _validate_workdir(workdir: str) -> str | None:
    if _WORKDIR_SAFE_RE.match(workdir):
        return None
    for ch in workdir:
        if not _WORKDIR_SAFE_RE.match(ch):
            return (
                f"Blocked: workdir contains disallowed character {ch!r}. "
                "Use a simple filesystem path without shell metacharacters."
            )
    return "Blocked: workdir contains disallowed characters."


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

TERMINAL_COMMON_DESCRIPTION = """Prefer available file tools for reading, searching, listing, writing and patching
when they operate on the required filesystem. Use terminal for builds, installs, git, processes,
scripts, network and shell tasks, or when the file tools cannot access the target environment.
Preserve the target host and paths; a local file tool does not establish the contents of a remote file.

Foreground (default): Commands return INSTANTLY when done, even if the timeout is high.
Set timeout=300 for long builds/scripts — you'll still get the result in seconds if it's fast.
Prefer foreground for short commands.
Background: Set background=true to get a session_id. A session_id means the process started,
not that its work succeeded. You are not notified when a background process exits; inspect its
status, output and exit code with an available process tool.
Servers and watchers normally stay silent. Check that process tools are available before relying
on later polling, stdin or termination; otherwise prefer foreground for bounded work.
For servers/watchers, do NOT use shell-level background wrappers (nohup/disown/setsid/trailing '&')
in foreground mode. Use background=true so SpiritAgent can track lifecycle and output.
After starting a server, verify readiness with a health check or log signal, then run tests in a
separate terminal() call. Avoid blind sleep loops.
Use process(action="poll") for progress checks, process(action="wait") to block until done.
Working directory: Use 'workdir' for per-command cwd.
Foreground commands receive no stdin, so interactive programs (editors, REPLs, prompts) cannot be
answered there. For interactive CLI tools on the local host, use background=true with pty=true and
send input through the process tool.
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


def _error_result(error: str, *, status: str = "error", exit_code: int = -1) -> str:
    return json.dumps({"output": "", "exit_code": exit_code, "error": error, "status": status}, ensure_ascii=False)


def _start_background(env: BaseEnvironment, command: str, cwd: str, task_id: str, pty: bool) -> str:
    result: dict[str, Any] = {}
    use_pty = pty
    if pty and _command_requires_pipe_stdin(command):
        use_pty = False
        result["pty_note"] = (
            "PTY disabled for this command because it expects piped stdin/EOF "
            "(for example gh auth login --with-token). Call process(action='close') "
            "after writing so it receives EOF."
        )
    try:
        if env.env_type == "local":
            session = process_registry.spawn_local(command=command, cwd=cwd, task_id=task_id, use_pty=use_pty)
        else:
            session = process_registry.spawn_via_env(env=env, command=command, cwd=cwd, task_id=task_id)
    except Exception as e:
        logger.warning("Background process failed to start: %s", e)
        return _error_result(f"Failed to start background process: {e}")
    result |= {
        "output": "Background process started",
        "session_id": session.id,
        "pid": session.pid,
        "exit_code": 0,
        "error": None,
    }
    return json.dumps(result, ensure_ascii=False)


def _run_foreground(env: BaseEnvironment, command: str, cwd: str, timeout: int) -> str:
    if is_interrupted():
        return _error_result("Command interrupted", status="cancelled", exit_code=130)
    result = env.execute(command, cwd=cwd, timeout=timeout)
    output = result["output"]
    returncode = result["returncode"]
    # 按字符预算，混用字节会撑破 CJK 载荷。
    max_output_chars = registry.get_max_result_size()
    if len(output) > max_output_chars:
        head_chars = int(max_output_chars * 0.4)
        tail_chars = max_output_chars - head_chars
        omitted = len(output) - head_chars - tail_chars
        truncated_notice = f"\n\n... [OUTPUT TRUNCATED - {omitted} chars omitted out of {len(output)} total] ...\n\n"
        output = output[:head_chars] + truncated_notice + output[-tail_chars:]
    # clean_output 同时去 ANSI 与脱敏。
    output = clean_output(output.strip()) if output else ""
    result_dict: dict[str, Any] = {
        "output": output,
        "exit_code": returncode,
        "error": f"Process exited with non-zero code {returncode}" if returncode != 0 else None,
    }
    if exit_note := _interpret_exit_code(command, returncode):
        result_dict["exit_code_meaning"] = exit_note
    return json.dumps(result_dict, ensure_ascii=False)


def terminal_tool(
    command: str,
    background: bool = False,
    timeout: int | None = None,
    task_id: str | None = None,
    workdir: str | None = None,
    pty: bool = False,
) -> str:
    """在对应 task 的终端环境中执行单条 shell 命令（前台或后台），必要时懒创建环境。

    取消经 ``is_interrupted()`` 感知（ContextVar 关联当前请求）：前台等待循环据此终止进程树。
    """
    try:
        if timeout is not None and timeout < 1:
            return _error_result("timeout must be at least 1")
        config = get_env_config()
        if blocked_host_error := _blocked_host_command_error(command, config["env_type"]):
            return _error_result(blocked_host_error, status="blocked")
        if workdir and (workdir_error := _validate_workdir(workdir)):
            logger.warning("Blocked dangerous workdir: %s (command: %s)", workdir[:200], command[:200])
            return _error_result(workdir_error, status="blocked")
        if not background:
            max_timeout = _foreground_max_timeout()
            if timeout and timeout > max_timeout:
                return _error_result(
                    f"Foreground timeout {timeout}s exceeds the maximum of {max_timeout}s. "
                    "Use background=true for long-running commands.",
                )
            if guidance := _foreground_background_guidance(command):
                return _error_result(guidance)
        effective_task_id = resolve_container_task_id(task_id)
        with use_environment(effective_task_id) as env:
            cwd = workdir or env.cwd
            if background:
                return _start_background(env, command, cwd, effective_task_id, pty)
            return _run_foreground(env, command, cwd, timeout or config["timeout"])
    except EnvironmentBusyError as e:
        return _error_result(str(e))
    except Exception as e:
        # traceback 只进日志，不回给模型。
        logger.exception("terminal_tool failed")
        return _error_result(f"Failed to execute command: {type(e).__name__}: {e}")


_TERMINAL_SCHEMA_TEMPLATE: dict[str, Any] = {
    "name": "terminal",
    "description": "",
    "parameters": {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": ""},
            "background": {
                "type": "boolean",
                "description": (
                    "Run the command in the background and return a session_id immediately. Use for "
                    "servers, watchers and other long-running work, then check status, output and exit "
                    "code with the process tool. For bounded commands, prefer foreground with a generous timeout."
                ),
                "default": False,
            },
            "timeout": {
                "type": "integer",
                "description": (
                    "Max seconds to wait for a foreground command (default: 180). Returns INSTANTLY when "
                    "the command finishes — set high for long tasks, you won't wait unnecessarily. "
                    "Foreground timeouts above the cap (600s by default) are rejected; use background=true "
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
                    "Run a background command on the local host in a pseudo-terminal (PTY), for interactive "
                    "CLI tools like Codex, Claude Code, or Python REPL; send input with the process tool. "
                    "Has no effect on foreground commands or remote hosts. Default: false."
                ),
                "default": False,
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
    command = args.get("command")
    if not isinstance(command, str):
        return _error_result(f"Invalid command: expected string, got {type(command).__name__}")
    timeout = args.get("timeout")
    if timeout is not None:
        try:
            timeout = int(timeout)
        except (TypeError, ValueError):
            return _error_result(f"timeout must be an integer, got {timeout!r}")
    return terminal_tool(
        command=command,
        background=bool(args.get("background", False)),
        timeout=timeout,
        task_id=kw.get("task_id"),
        workdir=args.get("workdir"),
        pty=bool(args.get("pty", False)),
    )


def _current_terminal_schema() -> dict[str, Any]:
    # 列表时读取，与执行时 env_type 一致。
    return build_terminal_schema(env_type=get_env_config()["env_type"])


registry.register_tool("terminal", schema=_current_terminal_schema)(_handle_terminal)
