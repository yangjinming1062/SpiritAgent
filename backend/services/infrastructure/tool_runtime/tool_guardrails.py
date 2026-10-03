import json
from dataclasses import dataclass
from typing import Any

from components import safe_json_loads, sha256_hex

from .file_safety import get_read_block_error, is_write_denied

# 只读工具：同参数反复得到相同结果即视为无进展。
_IDEMPOTENT_TOOL_NAMES = frozenset(
    {
        "read_file",
        "search_files",
        "web_search",
        "web_extract",
        "browser_snapshot",
        "browser_console",
        "browser_get_images",
        "browser_wait_for",
        "browser_find",
        "browser_storage_get",
        "browser_cookies_get",
        "browser_pdf",
        "browser_screenshot_element",
        "browser_tab_list",
    },
)

# terminal 故意不在此列——其危险面是命令字符串里的 shell 重定向（> ~/.ssh/authorized_keys），不是显式路径参数。runner 侧对命令文本做独立扫描；后端仅扫路径参数无法在不解析 shell 的前提下覆盖
_FILE_PATH_TOOLS = frozenset({"write_file", "patch", "read_file", "search_files"})
_FILE_PATH_ARG_NAMES = ("path", "file_path", "filepath", "target", "filename")
_WRITE_DENIED_TOOLS = frozenset({"write_file", "patch"})

_EXACT_FAILURE_WARN_AFTER = 2
_SAME_TOOL_FAILURE_WARN_AFTER = 3
_NO_PROGRESS_WARN_AFTER = 2


def check_file_safety(tool_name: str, args: dict[str, Any]) -> str | None:
    """派发前运行文件安全黑名单；命中时返回代替工具执行结果的拦截说明。"""
    if tool_name not in _FILE_PATH_TOOLS:
        return None
    for arg_name in _FILE_PATH_ARG_NAMES:
        path = args.get(arg_name)
        if not (isinstance(path, str) and path):
            continue
        if tool_name in _WRITE_DENIED_TOOLS and is_write_denied(path):
            return _blocked_result(
                tool_name,
                "write_denied",
                f"Blocked {tool_name}: {path} is in the write denylist (SSH credentials, SpiritAgent control plane, .env files). This is a backend-side safety check before runner dispatch.",
            )
        if (read_err := get_read_block_error(path)) is not None:
            return _blocked_result(tool_name, "read_denied", read_err)
    return None


def _blocked_result(tool_name: str, code: str, message: str) -> str:
    guardrail = {"action": "block", "code": code, "message": message, "tool_name": tool_name, "count": 0}
    return json.dumps({"error": message, "guardrail": guardrail}, ensure_ascii=False)


@dataclass(frozen=True)
class _LoopWarning:
    code: str
    count: int
    message: str


class ToolCallGuardrailController:
    """单回合内识别反复失败或无进展的工具调用，并在工具结果后追加换策略提示。"""

    def __init__(self) -> None:
        self._exact_failure_counts: dict[tuple[str, str], int] = {}
        self._same_tool_failure_counts: dict[str, int] = {}
        self._no_progress: dict[tuple[str, str], tuple[str, int]] = {}

    def record_call(self, tool_name: str, args: dict[str, Any], result: str) -> str:
        """记录原始结果，返回独立于不可信工具内容的循环提示。"""
        signature = (tool_name, _hash_json(args))
        if _tool_failed(result):
            warning = self._record_failure(tool_name, signature)
        else:
            warning = self._record_success(tool_name, signature, result)
        return (
            ""
            if warning is None
            else f"\n\n[Tool loop warning: {warning.code}; count={warning.count}; {warning.message}]"
        )

    def _record_failure(self, tool_name: str, signature: tuple[str, str]) -> _LoopWarning | None:
        exact_count = self._exact_failure_counts.get(signature, 0) + 1
        self._exact_failure_counts[signature] = exact_count
        self._no_progress.pop(signature, None)

        same_count = self._same_tool_failure_counts.get(tool_name, 0) + 1
        self._same_tool_failure_counts[tool_name] = same_count

        if exact_count >= _EXACT_FAILURE_WARN_AFTER:
            return _LoopWarning(
                "repeated_exact_failure_warning",
                exact_count,
                f"{tool_name} has failed {exact_count} times with identical arguments. This looks like a loop; inspect the error and change strategy instead of retrying it unchanged.",
            )
        if same_count >= _SAME_TOOL_FAILURE_WARN_AFTER:
            return _LoopWarning(
                "same_tool_failure_warning",
                same_count,
                _tool_failure_recovery_hint(tool_name, same_count),
            )
        return None

    def _record_success(self, tool_name: str, signature: tuple[str, str], result: str) -> _LoopWarning | None:
        self._exact_failure_counts.pop(signature, None)
        self._same_tool_failure_counts.pop(tool_name, None)

        if tool_name not in _IDEMPOTENT_TOOL_NAMES:
            self._no_progress.pop(signature, None)
            return None

        result_hash = _result_hash(result)
        previous = self._no_progress.get(signature)
        repeat_count = 1 if previous is None or previous[0] != result_hash else previous[1] + 1
        self._no_progress[signature] = (result_hash, repeat_count)

        if repeat_count >= _NO_PROGRESS_WARN_AFTER:
            return _LoopWarning(
                "idempotent_no_progress_warning",
                repeat_count,
                f"{tool_name} returned the same result {repeat_count} times. Use the result already provided or change the query instead of repeating it unchanged.",
            )
        return None


def _tool_failed(result: str) -> bool:
    """按结果信封判定失败：顶层 error 非空，或 success/ok 为 False。只看顶层字段：嵌套数据不代表调用失败，非 JSON 文本结果视为成功；终端非零退出码由 Runner 写入顶层 error。"""
    data = safe_json_loads(result)
    return isinstance(data, dict) and (
        bool(data.get("error")) or data.get("success") is False or data.get("ok") is False
    )


def _tool_failure_recovery_hint(tool_name: str, count: int) -> str:
    """针对反复工具失败给出可操作的恢复指引。"""
    common = (
        f"{tool_name} has failed {count} times this turn. Inspect the latest error or output and check your assumptions "
        "before trying again; retry only with a changed approach, and do not repeat a call whose outcome is unknown until "
        "its effect is verified. If the blocker is external, such as an offline desktop or an unavailable service, stop "
        "retrying and tell the user what is blocked. "
    )
    if tool_name == "terminal":
        return (
            common
            + "For terminal failures, run a small diagnostic such as `pwd && ls -la`, then try an absolute path, a simpler "
            "command, a different working directory, or a file tool such as read_file/write_file/patch."
        )
    return (
        common
        + "Otherwise try different arguments, a narrower query or path, or a different tool that can make progress."
    )


def _hash_json(value: dict[str, Any]) -> str:
    return sha256_hex(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str))


def _result_hash(result: str) -> str:
    parsed = safe_json_loads(result)
    if isinstance(parsed, dict):
        return _hash_json(parsed)
    return sha256_hex(result if parsed is None else str(parsed))
