import re
from collections.abc import Callable
from typing import Any

from spirit_agent_runner.features.files.formatting import _restore_line_endings, _to_lf, _unified_diff
from spirit_agent_runner.features.files.models import (
    Hunk,
    HunkLine,
    OperationType,
    PatchOperation,
    PatchResult,
    _OpOutcome,
)
from spirit_agent_runner.features.files.operations import FileOperations
from spirit_agent_runner.infrastructure.text_edits import format_no_match_hint, fuzzy_find_and_replace

_HUNK_HINT_RE = re.compile(r"@@\s*(.+?)\s*@@")


_HEADER_PATTERNS: tuple[tuple[re.Pattern, OperationType], ...] = (
    (re.compile(r"\*\*\*\s*Update\s+File:\s*(.+)"), OperationType.UPDATE),
    (re.compile(r"\*\*\*\s*Add\s+File:\s*(.+)"), OperationType.ADD),
    (re.compile(r"\*\*\*\s*Delete\s+File:\s*(.+)"), OperationType.DELETE),
    (re.compile(r"\*\*\*\s*Move\s+File:\s*(.+?)\s*->\s*(.+)"), OperationType.MOVE),
)


def _flush(op: PatchOperation | None, hunk: Hunk | None, sink: list[PatchOperation]) -> None:
    if not op:
        return
    if hunk and hunk.lines:
        op.hunks.append(hunk)
    sink.append(op)


def _hunk_text(hunk: Hunk, prefix_set: str) -> str:
    return "\n".join(line.content for line in hunk.lines if line.prefix in prefix_set)


def _add_hunk_line(hunk: Hunk, line: str) -> None:
    if line.startswith("\\"):
        return
    if line[0] in "+- ":
        hunk.lines.append(HunkLine(line[0], line[1:]))
    else:
        hunk.lines.append(HunkLine(" ", line))


def parse_v4a_patch(patch_content: str) -> tuple[list[PatchOperation], str | None]:
    lines = patch_content.replace("\r\n", "\n").split("\n")
    operations: list[PatchOperation] = []
    start_idx, end_idx = -1, len(lines)
    for i, line in enumerate(lines):
        if start_idx == -1 and ("*** Begin Patch" in line or "***Begin Patch" in line):
            start_idx = i
        elif "*** End Patch" in line or "***End Patch" in line:
            end_idx = i
            break
    current_op: PatchOperation | None = None
    current_hunk: Hunk | None = None
    i = start_idx + 1
    while i < end_idx:
        line = lines[i]
        matched_op: OperationType | None = None
        for pat, op_type in _HEADER_PATTERNS:
            if m := pat.match(line):
                _flush(current_op, current_hunk, operations)
                if op_type is OperationType.MOVE:
                    current_op = PatchOperation(
                        operation=op_type,
                        file_path=m.group(1).strip(),
                        new_path=m.group(2).strip(),
                    )
                    _flush(current_op, None, operations)
                    current_op = current_hunk = None
                elif op_type is OperationType.ADD:
                    current_op = PatchOperation(operation=op_type, file_path=m.group(1).strip())
                    current_hunk = Hunk()
                elif op_type is OperationType.DELETE:
                    _flush(PatchOperation(operation=op_type, file_path=m.group(1).strip()), None, operations)
                    current_op = current_hunk = None
                else:
                    current_op = PatchOperation(operation=op_type, file_path=m.group(1).strip())
                    current_hunk = None
                matched_op = op_type
                break
        if matched_op is not None:
            i += 1
            continue
        if line.startswith("@@"):
            if current_op:
                if current_hunk and current_hunk.lines:
                    current_op.hunks.append(current_hunk)
                hint = _HUNK_HINT_RE.match(line)
                current_hunk = Hunk(context_hint=hint.group(1) if hint else None)
        elif current_op and line:
            if current_hunk is None:
                current_hunk = Hunk()
            _add_hunk_line(current_hunk, line)
        i += 1
    _flush(current_op, current_hunk, operations)
    if not operations:
        return operations, None
    errors = [
        msg
        for op in operations
        for msg in (
            "Operation with empty file path" if not op.file_path else "",
            f"UPDATE {op.file_path!r}: no hunks found" if op.operation is OperationType.UPDATE and not op.hunks else "",
            f"MOVE {op.file_path!r}: missing destination path (expected 'src -> dst')"
            if op.operation is OperationType.MOVE and not op.new_path
            else "",
        )
        if msg
    ]
    if errors:
        return [], "Parse error: " + "; ".join(errors)
    return operations, None


def _insert_at_hint(text: str, hint: str, insert: str) -> tuple[str, str | None]:
    """在 hint 所在行之后插入；返回 (新文本, 错误)，出错时原文不变。"""
    occurrences = text.count(hint)
    if occurrences == 0:
        return text, f"addition-only hunk context hint '{hint}' not found"
    if occurrences > 1:
        return text, (
            f"addition-only hunk context hint '{hint}' is ambiguous ({occurrences} occurrences) — "
            "provide a more unique hint"
        )
    eol = text.find("\n", text.find(hint))
    if eol == -1:
        return text + "\n" + insert, None
    return text[: eol + 1] + insert + "\n" + text[eol + 1 :], None


def _retry_windowed(content: str, search: str, replace: str, hint: str) -> tuple[str, str | None]:
    """以 hint 为中心的小窗口内重试模糊匹配。"""
    pos = content.find(hint)
    if pos == -1:
        return content, "context hint not found"
    start = max(0, pos - 500)
    end = min(len(content), pos + 2000)
    window_new, _count, _, error = fuzzy_find_and_replace(content[start:end], search, replace, replace_all=False)
    if error is not None:
        return content, error
    return content[:start] + window_new + content[end:], None


def _apply_hunks(content: str, hunks: list[Hunk]) -> tuple[str, str | None]:
    """依次应用 hunk，返回 (新内容, 错误)；校验与实际写入共用，结果一致。"""
    for hunk in hunks:
        search, replace = _hunk_text(hunk, " -"), _hunk_text(hunk, " +")
        if not search:
            if hunk.context_hint:
                content, error = _insert_at_hint(content, hunk.context_hint, replace)
                if error is not None:
                    return content, error
            else:
                content = content.rstrip("\n") + "\n" + replace + "\n"
            continue
        new_content, _count, _strategy, error = fuzzy_find_and_replace(content, search, replace, replace_all=False)
        if error is not None and hunk.context_hint:
            new_content, error = _retry_windowed(content, search, replace, hunk.context_hint)
        if error is not None:
            label = f"'{hunk.context_hint}'" if hunk.context_hint else "(no hint)"
            return content, f"hunk {label} not found — {error}" + format_no_match_hint(error, 0, search, content)
        content = new_content
    return content, None


def _validate_operations(operations: list[PatchOperation], file_ops: FileOperations) -> list[str]:
    """不落盘地模拟整份补丁，返回全部错误。"""
    errors: list[str] = []
    for op in operations:
        match op.operation:
            case OperationType.UPDATE:
                read_result = file_ops.read_file_raw(op.file_path)
                if read_result.error:
                    errors.append(f"{op.file_path}: {read_result.error}")
                elif (error := _apply_hunks(_to_lf(read_result.content)[0], op.hunks)[1]) is not None:
                    errors.append(f"{op.file_path}: {error}")
            case OperationType.ADD:
                if file_ops.exists(op.file_path):
                    errors.append(f"{op.file_path}: already exists — use '*** Update File:' to modify it")
            case OperationType.DELETE:
                if not file_ops.exists(op.file_path):
                    errors.append(f"{op.file_path}: file not found for deletion")
            case OperationType.MOVE:
                if not file_ops.exists(op.file_path):
                    errors.append(f"{op.file_path}: source file not found for move")
                if op.new_path and file_ops.exists(op.new_path):
                    errors.append(f"{op.new_path}: destination already exists — move would overwrite")
    return errors


def _apply_add(op: PatchOperation, file_ops: FileOperations) -> _OpOutcome:
    lines = [line.content for h in op.hunks for line in h.lines if line.prefix == "+"]
    content = "\n".join(lines) + "\n" if lines else ""
    if (result := file_ops.write_file(op.file_path, content)).error:
        return _OpOutcome(error=result.error)
    diff = f"--- /dev/null\n+++ b/{op.file_path}\n" + "\n".join(f"+{line}" for line in lines)
    return _OpOutcome(diff=diff, lint=result.lint)


def _apply_delete(op: PatchOperation, file_ops: FileOperations) -> _OpOutcome:
    before = file_ops.read_file_raw(op.file_path)
    if (result := file_ops.delete_file(op.file_path)).error:
        return _OpOutcome(error=result.error)
    diff = "" if before.error else _unified_diff(before.content, "", op.file_path)
    return _OpOutcome(diff=diff or f"# Deleted: {op.file_path}")


def _apply_move(op: PatchOperation, file_ops: FileOperations) -> _OpOutcome:
    if op.new_path is None:
        return _OpOutcome(error="missing destination path")
    if (result := file_ops.move_file(op.file_path, op.new_path)).error:
        return _OpOutcome(error=result.error)
    return _OpOutcome(diff=f"# Moved: {op.file_path} -> {op.new_path}")


def _apply_update(op: PatchOperation, file_ops: FileOperations) -> _OpOutcome:
    read_result = file_ops.read_file_raw(op.file_path)
    if read_result.error:
        return _OpOutcome(error=f"Cannot read file: {read_result.error}")
    content, ending = _to_lf(read_result.content)
    new_content, error = _apply_hunks(content, op.hunks)
    if error is not None:
        return _OpOutcome(error=f"Could not apply {error}")
    if (write_result := file_ops.write_file(op.file_path, _restore_line_endings(new_content, ending))).error:
        return _OpOutcome(error=write_result.error)
    return _OpOutcome(
        diff=_unified_diff(content, new_content, op.file_path),
        lint=write_result.lint,
    )


_APPLY: dict[OperationType, Callable[[PatchOperation, FileOperations], _OpOutcome]] = {
    OperationType.ADD: _apply_add,
    OperationType.UPDATE: _apply_update,
    OperationType.DELETE: _apply_delete,
    OperationType.MOVE: _apply_move,
}


def apply_v4a_operations(operations: list[PatchOperation], file_ops: FileOperations) -> PatchResult:
    """两阶段：先整体校验，全部通过后才依次落盘。"""
    if not operations:
        return PatchResult(
            error="Patch contained no operations (missing `*** Begin Patch` header or no file sections).",
        )
    if errors := _validate_operations(operations, file_ops):
        return PatchResult(
            error="Patch validation failed (no files were modified):\n" + "\n".join(f"  • {e}" for e in errors),
        )
    files_modified: list[str] = []
    files_created: list[str] = []
    files_deleted: list[str] = []
    all_diffs: list[str] = []
    lint: dict[str, Any] = {}
    apply_errors: list[str] = []
    for op in operations:
        # 失败不中断，最后统一汇报。
        try:
            outcome = _APPLY[op.operation](op, file_ops)
        except Exception as e:
            apply_errors.append(f"Error processing {op.file_path}: {e}")
            continue
        if outcome.error is not None:
            apply_errors.append(f"Failed to {op.operation.value} {op.file_path}: {outcome.error}")
            continue
        match op.operation:
            case OperationType.ADD:
                files_created.append(op.file_path)
            case OperationType.DELETE:
                files_deleted.append(op.file_path)
            case OperationType.MOVE:
                files_modified.append(f"{op.file_path} -> {op.new_path}")
            case OperationType.UPDATE:
                files_modified.append(op.file_path)
        if outcome.lint is not None:
            lint[op.file_path] = outcome.lint
        all_diffs.append(outcome.diff)
    return PatchResult(
        success=not apply_errors,
        diff="\n".join(all_diffs),
        files_modified=files_modified,
        files_created=files_created,
        files_deleted=files_deleted,
        lint=lint or None,
        error=(
            "Apply phase failed; files listed as modified/created/deleted were changed, the rest were not. "
            "Re-read the affected files before retrying:\n" + "\n".join(f"  • {e}" for e in apply_errors)
            if apply_errors
            else None
        ),
    )
