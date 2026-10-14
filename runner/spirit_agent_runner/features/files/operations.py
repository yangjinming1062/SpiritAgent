import os
from abc import ABC, abstractmethod
from typing import Any, Protocol

from spirit_agent_runner.features.files.formatting import _restore_line_endings, _strip_bom, _to_lf, _unified_diff
from spirit_agent_runner.features.files.linting import LINTERS_INPROC, _looks_like_linter_unusable
from spirit_agent_runner.features.files.models import (
    DEFAULT_READ_LIMIT,
    DEFAULT_READ_OFFSET,
    DEFAULT_SEARCH_LIMIT,
    DEFAULT_SEARCH_OFFSET,
    LINTERS,
    ExecuteResult,
    LintResult,
    ListResult,
    PatchResult,
    ReadResult,
    SearchResult,
    WriteResult,
)
from spirit_agent_runner.infrastructure.clean import strip_ansi
from spirit_agent_runner.infrastructure.text_edits import format_no_match_hint, fuzzy_find_and_replace


class TerminalEnv(Protocol):
    """文件操作依赖的终端环境能力；``cwd`` 随终端命令实时更新。"""

    cwd: str
    env_type: str

    def execute(
        self,
        command: str,
        cwd: str = "",
        *,
        timeout: int | None = None,
        stdin_data: str | None = None,
    ) -> dict[str, Any]: ...


class FileOperations(ABC):
    """终端环境上的文件操作接口；补丁与语法检查在此统一实现。"""

    def __init__(self, env: TerminalEnv) -> None:
        self.env = env

    @abstractmethod
    def read_file(self, path: str, offset: int = DEFAULT_READ_OFFSET, limit: int = DEFAULT_READ_LIMIT) -> ReadResult:
        """按行分页读取文本并加行号。"""

    @abstractmethod
    def read_file_raw(self, path: str) -> ReadResult:
        """读取完整 UTF-8 文本供编辑：保留原换行符、去掉 BOM；二进制或非 UTF-8 时返回错误。"""

    @abstractmethod
    def write_file(self, path: str, content: str) -> WriteResult:
        """原子写入；沿用已有文件的 CRLF 与 BOM，并对比写前内容做语法检查。"""

    @abstractmethod
    def exists(self, path: str) -> bool: ...

    @abstractmethod
    def delete_file(self, path: str) -> WriteResult: ...

    @abstractmethod
    def move_file(self, src: str, dst: str) -> WriteResult: ...

    @abstractmethod
    def list_directory(self, path: str) -> ListResult: ...

    @abstractmethod
    def search(
        self,
        pattern: str,
        path: str = ".",
        target: str = "content",
        file_glob: str | None = None,
        limit: int = DEFAULT_SEARCH_LIMIT,
        offset: int = DEFAULT_SEARCH_OFFSET,
        output_mode: str = "content",
        context: int = 0,
    ) -> SearchResult: ...

    @abstractmethod
    def _exec(self, command: str, timeout: int | None = None) -> ExecuteResult: ...

    @abstractmethod
    def _has_command(self, cmd: str) -> bool: ...

    @abstractmethod
    def _escape_shell_arg(self, arg: str) -> str: ...

    def patch_replace(self, path: str, old_string: str, new_string: str, replace_all: bool = False) -> PatchResult:
        """用模糊匹配替换文本；结果沿用文件原换行符，经 write_file 原子写入。"""
        read = self.read_file_raw(path)
        if read.error:
            return PatchResult(error=read.error)
        content, ending = _to_lf(read.content)
        old_string, new_string = _to_lf(old_string)[0], _to_lf(new_string)[0]
        new_content, _count, _strategy, error = fuzzy_find_and_replace(content, old_string, new_string, replace_all)
        if error is not None:
            return PatchResult(error=error + format_no_match_hint(error, 0, old_string, content))
        written = self.write_file(path, _restore_line_endings(new_content, ending))
        if written.error:
            return PatchResult(error=f"Failed to write changes: {written.error}")
        return PatchResult(
            success=True,
            diff=_unified_diff(content, new_content, path),
            files_modified=[path],
            lint=written.lint,
        )

    def _check_lint(self, path: str, content: str) -> LintResult:
        """语法检查：已知格式在进程内校验 ``content``，其他语言调用外部检查器检查磁盘上的文件。"""
        ext = os.path.splitext(path)[1].lower()
        if (inproc := LINTERS_INPROC.get(ext)) is not None:
            ok, err = inproc(_strip_bom(content)[0])
            return LintResult(success=ok, output="" if ok else err)
        if ext not in LINTERS:
            return LintResult(skipped=True, message=f"No linter for {ext} files")
        linter_cmd = LINTERS[ext]
        base_cmd = linter_cmd.split()[0]
        if not self._has_command(base_cmd):
            return LintResult(skipped=True, message=f"{base_cmd} not available")
        result = self._exec(linter_cmd.replace("{file}", self._escape_shell_arg(path)), timeout=30)
        if result.exit_code != 0 and _looks_like_linter_unusable(base_cmd, result.stdout):
            cleaned = strip_ansi(result.stdout).strip()
            first_line = next((ln.strip() for ln in cleaned.splitlines() if ln.strip()), cleaned[:120])
            return LintResult(skipped=True, message=f"{base_cmd} not usable: {first_line[:200]}")
        return LintResult(success=result.exit_code == 0, output=result.stdout.strip())

    def _check_lint_delta(self, path: str, pre_content: str | None, post_content: str) -> LintResult:
        """写后语法检查；进程内检查器能拿写前内容做基线时，只报告本次新增的错误。"""
        post = self._check_lint(path, post_content)
        inproc = LINTERS_INPROC.get(os.path.splitext(path)[1].lower())
        if post.success or post.skipped or pre_content is None or inproc is None:
            return post
        pre_ok, pre_output = inproc(_strip_bom(pre_content)[0])
        if pre_ok or not pre_output:
            return post
        pre_lines = {ln.strip() for ln in pre_output.splitlines() if ln.strip()}
        post_lines = [ln for ln in post.output.splitlines() if ln.strip() and ln.strip() not in pre_lines]
        if not post_lines:
            return LintResult(
                success=False,
                output=post.output,
                message="Pre-existing lint errors — this edit didn't introduce new ones but the file is still broken.",
            )
        return LintResult(
            success=False,
            output=(
                "New lint errors introduced by this edit (pre-existing errors filtered out):\n" + "\n".join(post_lines)
            ),
        )
