import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from components import get_logger, safe_json_loads, sha256_hex

from .file_safety import get_read_block_error, is_write_denied
from .tool_dispatch_helpers import _append_subdir_hint_to_multimodal

logger = get_logger(__name__)

IDEMPOTENT_TOOL_NAMES = frozenset(
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

MUTATING_TOOL_NAMES = frozenset(
    {
        "terminal",
        "execute_code",
        "write_file",
        "patch",
        "todo",
        "skill_manage",
        "browser_click",
        "browser_type",
        "browser_press",
        "browser_scroll",
        "browser_navigate",
        "browser_hover",
        "browser_drag",
        "browser_select",
        "browser_download",
        "browser_cookies_set",
        "browser_cookies_clear",
        "browser_storage_set",
        "browser_tab_new",
        "browser_tab_switch",
        "browser_tab_close",
        "browser_set_viewport",
        "browser_set_user_agent",
        "browser_set_extra_headers",
        "browser_set_geolocation",
        "send_message",
        "cronjob",
        "process",
    },
)

# ``terminal`` 故意不在此列——其危险面是命令字符串里的 shell 重定向（``> ~/.ssh/authorized_keys``），不是显式路径参数。runner 侧对命令文本做独立扫描来堵住这种绕过；后端仅扫路径参数无法在不解析 shell 的前提下覆盖。
_FILE_PATH_TOOLS = frozenset({"write_file", "patch", "read_file", "search_files"})
_FILE_PATH_ARG_NAMES = ("path", "file_path", "filepath", "target", "filename")
_WRITE_DENIED_TOOLS = frozenset({"write_file", "patch"})

_MEMORY_FULL_PHRASES = (
    "exceed the limit",
    "exceeds the limit",
    "exceed the maximum",
    "exceeds the maximum",
    "size limit exceeded",
    "limit exceeded",
    "over the limit",
    "over quota",
    "quota exceeded",
    "memory full",
    "storage full",
    "too large",
)


def check_file_safety(tool_name: str, args: Mapping[str, Any] | None) -> "ToolGuardrailDecision | None":
    """派发前运行文件安全黑名单，返回 ``block`` 决策或 None；纯函数，调用方决定如何呈现。"""
    if tool_name not in _FILE_PATH_TOOLS or not isinstance(args, Mapping):
        return None
    for arg_name in _FILE_PATH_ARG_NAMES:
        path = args.get(arg_name)
        if not (isinstance(path, str) and path):
            continue
        if tool_name in _WRITE_DENIED_TOOLS and is_write_denied(path):
            return ToolGuardrailDecision(
                action="block",
                code="write_denied",
                message=(
                    f"Blocked {tool_name}: {path} is in the write denylist (SSH credentials, SpiritAgent control plane, .env files). This is a backend-side safety check before runner dispatch."
                ),
                tool_name=tool_name,
            )
        if (read_err := get_read_block_error(path)) is not None:
            return ToolGuardrailDecision(action="block", code="read_denied", message=read_err, tool_name=tool_name)
    return None


@dataclass(frozen=True)
class ToolCallGuardrailConfig:
    """单回合循环检测阈值；警告默认开启，硬停止需显式开启。"""

    warnings_enabled: bool = True
    hard_stop_enabled: bool = False
    exact_failure_warn_after: int = 2
    exact_failure_block_after: int = 5
    same_tool_failure_warn_after: int = 3
    same_tool_failure_halt_after: int = 8
    no_progress_warn_after: int = 2
    no_progress_block_after: int = 5
    idempotent_tools: frozenset[str] = field(default_factory=lambda: IDEMPOTENT_TOOL_NAMES)
    mutating_tools: frozenset[str] = field(default_factory=lambda: MUTATING_TOOL_NAMES)


@dataclass(frozen=True)
class ToolCallSignature:
    """工具名 + 规范化参数 的稳定、不可逆标识。"""

    tool_name: str
    args_hash: str

    @classmethod
    def from_call(cls, tool_name: str, args: Mapping[str, Any] | None) -> "ToolCallSignature":
        return cls(tool_name=tool_name, args_hash=sha256_hex(canonical_tool_args(args or {})))

    def to_metadata(self) -> dict[str, str]:
        return {"tool_name": self.tool_name, "args_hash": self.args_hash}


@dataclass(frozen=True)
class ToolGuardrailDecision:
    """工具调用守卫控制器返回的决策。"""

    action: str = "allow"  # allow | warn | block | halt
    code: str = "allow"
    message: str = ""
    tool_name: str = ""
    count: int = 0
    signature: ToolCallSignature | None = None

    @property
    def should_halt(self) -> bool:
        return self.action in {"block", "halt"}

    def to_metadata(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "action": self.action,
            "code": self.code,
            "message": self.message,
            "tool_name": self.tool_name,
            "count": self.count,
        }
        if self.signature is not None:
            data["signature"] = self.signature.to_metadata()
        return data


def canonical_tool_args(args: Mapping[str, Any]) -> str:
    """对解析后的工具参数生成排序紧凑 JSON。"""
    if not isinstance(args, Mapping):
        raise TypeError(f"tool args must be a mapping, got {type(args).__name__}")
    return json.dumps(args, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def classify_tool_failure(tool_name: str, result: str | None) -> tuple[bool, str]:
    """未显式传 ``failed=`` 时的安全兜底分类器——生产调用方总会传 ``failed=``，这里仅为测试与工具脚本提供一致行为。"""
    if result is None:
        return False, ""

    lower = result[:500].lower()
    if "successfully written" in lower or "successfully patched" in lower:
        return False, ""

    if tool_name == "terminal":
        data = safe_json_loads(result)
        if isinstance(data, dict) and (exit_code := data.get("exit_code")) is not None and exit_code != 0:
            return True, f" [exit {exit_code}]"
        return False, ""

    if tool_name == "memory":
        data = safe_json_loads(result)
        if isinstance(data, dict) and data.get("success") is False:
            error_msg = str(data.get("error", "")).lower()
            if any(phrase in error_msg for phrase in _MEMORY_FULL_PHRASES):
                return True, " [full]"

    if '"error"' in lower or '"failed"' in lower or result.startswith("Error"):
        return True, " [error]"

    return False, ""


class ToolCallGuardrailController:
    """单回合内对反复失败/无进展的工具调用进行控制。"""

    def __init__(self, config: ToolCallGuardrailConfig | None = None) -> None:
        self.config = config or ToolCallGuardrailConfig()
        self.reset_for_turn()

    def reset_for_turn(self) -> None:
        self._exact_failure_counts: dict[ToolCallSignature, int] = {}
        self._same_tool_failure_counts: dict[str, int] = {}
        self._no_progress: dict[ToolCallSignature, tuple[str, int]] = {}
        self._halt_decision: ToolGuardrailDecision | None = None

    @property
    def halt_decision(self) -> ToolGuardrailDecision | None:
        return self._halt_decision

    def before_call(self, tool_name: str, args: Mapping[str, Any] | None) -> ToolGuardrailDecision:
        signature = ToolCallSignature.from_call(tool_name, _coerce_args(args))
        if not self.config.hard_stop_enabled:
            return ToolGuardrailDecision(tool_name=tool_name, signature=signature)

        exact_count = self._exact_failure_counts.get(signature, 0)
        if exact_count >= self.config.exact_failure_block_after:
            decision = ToolGuardrailDecision(
                action="block",
                code="repeated_exact_failure_block",
                message=(
                    f"Blocked {tool_name}: the same tool call failed {exact_count} times with identical arguments. Stop retrying it unchanged; change strategy or explain the blocker."
                ),
                tool_name=tool_name,
                count=exact_count,
                signature=signature,
            )
            self._halt_decision = decision
            return decision

        if (
            self._is_idempotent(tool_name)
            and (record := self._no_progress.get(signature)) is not None
            and (repeat_count := record[1]) >= self.config.no_progress_block_after
        ):
            decision = ToolGuardrailDecision(
                action="block",
                code="idempotent_no_progress_block",
                message=(
                    f"Blocked {tool_name}: this read-only call returned the same result {repeat_count} times. Stop repeating it unchanged; use the result already provided or try a different query."
                ),
                tool_name=tool_name,
                count=repeat_count,
                signature=signature,
            )
            self._halt_decision = decision
            return decision

        return ToolGuardrailDecision(tool_name=tool_name, signature=signature)

    def after_call(
        self,
        tool_name: str,
        args: Mapping[str, Any] | None,
        result: str | None,
        *,
        failed: bool | None = None,
        signature: ToolCallSignature | None = None,
    ) -> ToolGuardrailDecision:
        args = _coerce_args(args)
        signature = signature or ToolCallSignature.from_call(tool_name, args)
        if failed is None:
            failed, _ = classify_tool_failure(tool_name, result)
        return (
            self._record_failure(tool_name, signature) if failed else self._record_success(tool_name, signature, result)
        )

    def _record_failure(self, tool_name: str, signature: ToolCallSignature) -> ToolGuardrailDecision:
        exact_count = self._exact_failure_counts.get(signature, 0) + 1
        self._exact_failure_counts[signature] = exact_count
        self._no_progress.pop(signature, None)

        same_count = self._same_tool_failure_counts.get(tool_name, 0) + 1
        self._same_tool_failure_counts[tool_name] = same_count

        if self.config.hard_stop_enabled and same_count >= self.config.same_tool_failure_halt_after:
            decision = ToolGuardrailDecision(
                action="halt",
                code="same_tool_failure_halt",
                message=(
                    f"Stopped {tool_name}: it failed {same_count} times this turn. Stop retrying the same failing tool path and choose a different approach."
                ),
                tool_name=tool_name,
                count=same_count,
                signature=signature,
            )
            self._halt_decision = decision
            return decision

        if self.config.warnings_enabled and exact_count >= self.config.exact_failure_warn_after:
            return ToolGuardrailDecision(
                action="warn",
                code="repeated_exact_failure_warning",
                message=(
                    f"{tool_name} has failed {exact_count} times with identical arguments. This looks like a loop; inspect the error and change strategy instead of retrying it unchanged."
                ),
                tool_name=tool_name,
                count=exact_count,
                signature=signature,
            )

        if self.config.warnings_enabled and same_count >= self.config.same_tool_failure_warn_after:
            return ToolGuardrailDecision(
                action="warn",
                code="same_tool_failure_warning",
                message=_tool_failure_recovery_hint(tool_name, same_count),
                tool_name=tool_name,
                count=same_count,
                signature=signature,
            )

        return ToolGuardrailDecision(tool_name=tool_name, count=exact_count, signature=signature)

    def _record_success(
        self,
        tool_name: str,
        signature: ToolCallSignature,
        result: str | None,
    ) -> ToolGuardrailDecision:
        self._exact_failure_counts.pop(signature, None)
        self._same_tool_failure_counts.pop(tool_name, None)

        if not self._is_idempotent(tool_name):
            self._no_progress.pop(signature, None)
            return ToolGuardrailDecision(tool_name=tool_name, signature=signature)

        result_hash = _result_hash(result)
        previous = self._no_progress.get(signature)
        repeat_count = 1 if previous is None or previous[0] != result_hash else previous[1] + 1
        self._no_progress[signature] = (result_hash, repeat_count)

        if self.config.warnings_enabled and repeat_count >= self.config.no_progress_warn_after:
            return ToolGuardrailDecision(
                action="warn",
                code="idempotent_no_progress_warning",
                message=(
                    f"{tool_name} returned the same result {repeat_count} times. Use the result already provided or change the query instead of repeating it unchanged."
                ),
                tool_name=tool_name,
                count=repeat_count,
                signature=signature,
            )

        return ToolGuardrailDecision(tool_name=tool_name, count=repeat_count, signature=signature)

    def _is_idempotent(self, tool_name: str) -> bool:
        return tool_name not in self.config.mutating_tools and tool_name in self.config.idempotent_tools


def toolguard_synthetic_result(decision: ToolGuardrailDecision) -> str:
    """为被拦截的工具调用合成 ``role=tool`` 内容。"""
    return json.dumps({"error": decision.message, "guardrail": decision.to_metadata()}, ensure_ascii=False)


def append_toolguard_guidance(result: str, decision: ToolGuardrailDecision) -> str:
    """向工具结果追加运行时提示；multimodal 包裹会把提示加到第一个文本段。"""
    if decision.action not in {"warn", "halt"} or not decision.message:
        return result
    label = "Tool loop hard stop" if decision.action == "halt" else "Tool loop warning"
    suffix = f"\n\n[{label}: {decision.code}; count={decision.count}; {decision.message}]"

    if result and result.lstrip().startswith("{"):
        try:
            parsed = json.loads(result)
            if isinstance(parsed, dict) and parsed.get("_multimodal") is True:
                _append_subdir_hint_to_multimodal(parsed, suffix)
                return json.dumps(parsed, ensure_ascii=False)
        except Exception:
            logger.debug("failed to parse tool result as JSON for multimodal hint", exc_info=True)

    return (result or "") + suffix


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


def _coerce_args(args: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return args if isinstance(args, Mapping) else {}


def _result_hash(result: str | None) -> str:
    parsed = safe_json_loads(result or "")
    if isinstance(parsed, Mapping):
        try:
            canonical = canonical_tool_args(parsed)
        except TypeError:
            canonical = str(parsed)
    elif parsed is None:
        canonical = result or ""
    else:
        canonical = str(parsed)
    return sha256_hex(canonical)
