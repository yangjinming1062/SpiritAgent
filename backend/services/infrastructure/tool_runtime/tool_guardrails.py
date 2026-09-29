import json
from dataclasses import dataclass
from typing import Any

from components import safe_json_loads, sha256_hex

from .file_safety import get_read_block_error, is_write_denied
from .tool_dispatch_helpers import is_multimodal_tool_result

# 只读工具：同参数反复得到相同结果即视为无进展。
_IDEMPOTENT_TOOL_NAMES = frozenset(
    {
        "read_file",
        "search_files",
        "web_search",
        "web_extract",
        "session_search",
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

# ``terminal`` 故意不在此列——其危险面是命令字符串里的 shell 重定向（``> ~/.ssh/authorized_keys``），不是显式路径参数。runner 侧对命令文本做独立扫描来堵住这种绕过；后端仅扫路径参数无法在不解析 shell 的前提下覆盖。
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

    def after_call(self, tool_name: str, args: dict[str, Any], result: str) -> str:
        """记录本次调用结果，返回（必要时）追加了循环提示的工具结果。"""
        signature = (tool_name, _hash_json(args))
        if _tool_failed(result):
            warning = self._record_failure(tool_name, signature)
        else:
            warning = self._record_success(tool_name, signature, result)
        return result if warning is None else _append_warning(result, warning)

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
    """按结果信封判定失败：顶层 ``error`` 非空，或 ``success`` / ``ok`` 为 False。

    只看顶层字段：写入结果里的 lint 状态、读到的文件内容等嵌套数据不代表调用失败，非 JSON 文本结果视为成功。
    终端非零退出码由 Runner 写入顶层 ``error``。
    """
    data = safe_json_loads(result)
    return isinstance(data, dict) and (
        bool(data.get("error")) or data.get("success") is False or data.get("ok") is False
    )


def _append_warning(result: str, warning: _LoopWarning) -> str:
    """向工具结果追加循环提示；multimodal 包裹把提示加到第一个文本段与摘要。"""
    suffix = f"\n\n[Tool loop warning: {warning.code}; count={warning.count}; {warning.message}]"
    parsed = safe_json_loads(result) if result.lstrip().startswith("{") else None
    if not is_multimodal_tool_result(parsed):
        return result + suffix
    parts = parsed["content"]
    for part in parts:
        if isinstance(part, dict) and part.get("type") == "input_text":
            part["text"] = str(part.get("text", "")) + suffix
            break
    else:
        parts.insert(0, {"type": "input_text", "text": suffix})
    if isinstance(parsed.get("text_summary"), str):
        parsed["text_summary"] += suffix
    return json.dumps(parsed, ensure_ascii=False)


def _tool_failure_recovery_hint(tool_name: str, count: int) -> str:
    """针对反复工具失败给出可操作的恢复指引。"""
    common = f"{tool_name} has failed {count} times this turn. This looks like a loop. Do not switch to text-only replies; keep using tools, but diagnose before retrying. First inspect the latest error/output and verify your assumptions. "
    if tool_name == "terminal":
        return (
            common
            + "For terminal failures, run a small diagnostic such as `pwd && ls -la` in the same tool, then try an absolute path, a simpler command, a different working directory, or a different tool such as read_file/write_file/patch."
        )
    return (
        common
        + "Try different arguments, a narrower query/path, an absolute path when relevant, or a different tool that can make progress. If the blocker is external, report the blocker after one diagnostic attempt instead of repeating the same failing path."
    )


def _hash_json(value: dict[str, Any]) -> str:
    return sha256_hex(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str))


def _result_hash(result: str) -> str:
    parsed = safe_json_loads(result)
    if isinstance(parsed, dict):
        return _hash_json(parsed)
    return sha256_hex(result if parsed is None else str(parsed))
