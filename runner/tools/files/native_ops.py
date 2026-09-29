import fnmatch
import os
import re
import shlex
import shutil
import stat
import subprocess
from collections.abc import Iterator
from pathlib import Path

from utils import CREATE_NO_WINDOW, IS_WINDOWS, atomic_replace, is_write_denied, msys_to_windows_path

from .binary_extensions import has_binary_extension
from .helpers import (
    BINARY_FILE_ERROR,
    DEFAULT_READ_LIMIT,
    DEFAULT_READ_OFFSET,
    DEFAULT_SEARCH_LIMIT,
    DEFAULT_SEARCH_OFFSET,
    LINTERS_INPROC,
    MAX_EDIT_BYTES,
    MAX_MATCH_CONTENT,
    ExecuteResult,
    FileOperations,
    ListResult,
    ReadResult,
    SearchMatch,
    SearchResult,
    WriteResult,
    _not_utf8_error,
    _strip_bom,
    build_read_result,
    is_binary_content,
    match_existing_format,
    rank_similar_names,
    too_large_to_edit_error,
)

# 内容搜索最多读取的文件数，避免在大目录上长时间阻塞。
_MAX_SEARCH_FILES = 1000


def _glob_match(rel_posix: str, pattern: str) -> bool:
    """模式不含 ``/`` 时只匹配文件名；含 ``/`` 时匹配相对路径，``**/`` 前缀也可匹配零层目录。"""
    if "/" not in pattern:
        return fnmatch.fnmatch(rel_posix.rsplit("/", 1)[-1], pattern)
    return fnmatch.fnmatch(rel_posix, pattern) or (
        pattern.startswith("**/") and fnmatch.fnmatch(rel_posix, pattern[3:])
    )


def _iter_files(root: Path) -> Iterator[tuple[Path, str]]:
    """遍历 ``root`` 下的文件，返回 (路径, 相对 posix 路径)；与 rg 默认一致，跳过隐藏文件和隐藏目录。"""
    if not root.is_dir():
        yield root, root.name
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in sorted(filenames):
            if not name.startswith("."):
                p = Path(dirpath) / name
                yield p, p.relative_to(root).as_posix()


def _match_content(lines: list[str], i: int, context: int) -> str:
    if context <= 0:
        return lines[i][:MAX_MATCH_CONTENT]
    start, end = max(0, i - context), min(len(lines), i + context + 1)
    return "\n".join(f"{j + 1}{':' if j == i else '-'}{lines[j][:MAX_MATCH_CONTENT]}" for j in range(start, end))


class NativeFileOperations(FileOperations):
    """本地环境：用 Python 原生 I/O 操作宿主机文件，相对路径按终端当前目录解析。"""

    def resolve_path(self, path: str, *, follow_symlinks: bool = True) -> Path:
        """解析为宿主机绝对路径：展开 ``~``，Windows 上转换 MSYS 风格路径。

        ``follow_symlinks=False`` 时只解析父目录，删除或移动符号链接时作用于链接本身。
        """
        p = Path(msys_to_windows_path(path)).expanduser()
        if not p.is_absolute():
            p = Path(self.env.cwd) / p
        return p.resolve() if follow_symlinks else p.parent.resolve() / p.name

    def _not_found(self, p: Path, path: str) -> ReadResult:
        try:
            names = os.listdir(p.parent)
        except OSError:
            names = []
        similar = [str(p.parent / n) for n in rank_similar_names(names, p.name)]
        return ReadResult(error=f"File not found: {path}", similar_files=similar)

    def read_file(self, path: str, offset: int = DEFAULT_READ_OFFSET, limit: int = DEFAULT_READ_LIMIT) -> ReadResult:
        p = self.resolve_path(path)
        if not p.exists():
            return self._not_found(p, path)
        if p.is_dir():
            return ReadResult(error=f"Path is a directory: '{path}'. Use list_directory instead.")
        # FIFO、设备等非普通文件读取可能永久阻塞。
        if not p.is_file():
            return ReadResult(error=f"Not a regular file: '{path}'.")
        end_line = offset + limit - 1
        lines: list[str] = []
        total_lines = 0
        try:
            file_size = p.stat().st_size
            with p.open("rb") as f:
                sample = f.read(1000)
            if is_binary_content(str(p), sample.decode("utf-8", errors="replace")):
                return ReadResult(is_binary=True, file_size=file_size, error=BINARY_FILE_ERROR)
            with p.open(encoding="utf-8", errors="replace") as f:
                for total_lines, line in enumerate(f, start=1):
                    if offset <= total_lines <= end_line:
                        lines.append(line)
        except OSError as e:
            return ReadResult(error=f"Error reading file: {e}")
        return build_read_result("".join(lines), offset, limit, total_lines, file_size)

    def read_file_raw(self, path: str) -> ReadResult:
        p = self.resolve_path(path)
        if not p.is_file():
            return ReadResult(error=f"Not a regular file: '{path}'.") if p.exists() else self._not_found(p, path)
        try:
            if (size := p.stat().st_size) > MAX_EDIT_BYTES:
                return ReadResult(file_size=size, error=too_large_to_edit_error(path, size))
            data = p.read_bytes()
        except OSError as e:
            return ReadResult(error=f"Failed to read file: {e}")
        if is_binary_content(str(p), data[:1000].decode("utf-8", errors="replace")):
            return ReadResult(is_binary=True, error=BINARY_FILE_ERROR)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return ReadResult(error=_not_utf8_error(path))
        content, _ = _strip_bom(text)
        return ReadResult(content=content, file_size=len(data))

    def write_file(self, path: str, content: str) -> WriteResult:
        p = self.resolve_path(path)
        if is_write_denied(str(p)):
            return WriteResult(error=f"Write denied: '{path}' is a protected system/credential file.")
        if p.is_dir():
            return WriteResult(error=f"Path is a directory: '{path}'. Use a file path, not a directory.")
        pre_content: str | None = None
        existing: str | None = None
        if p.is_file():
            # 进程内语法检查需要完整的写前内容做基线；其他类型或超大文件只取开头判断换行符与 BOM。
            # 按原始字节解码以保留 CRLF。
            try:
                full = p.suffix.lower() in LINTERS_INPROC and p.stat().st_size <= MAX_EDIT_BYTES
                with p.open("rb") as f:
                    existing = (f.read() if full else f.read(4096)).decode("utf-8", errors="replace")
                pre_content = existing if full else None
            except OSError:
                # 读不到原文件时无法沿用其格式；写入本身若同样无权限，会在下方报错。
                existing = None
        content = match_existing_format(content, existing)
        dirs_created = not p.parent.exists()
        try:
            atomic_replace(str(p), content)
        except (OSError, UnicodeEncodeError) as e:
            return WriteResult(error=f"Failed to write file: {e}")
        lint_result = self._check_lint_delta(str(p), pre_content, content)
        return WriteResult(
            bytes_written=len(content.encode("utf-8")),
            dirs_created=dirs_created,
            lint=lint_result.to_dict(),
        )

    def exists(self, path: str) -> bool:
        return os.path.lexists(self.resolve_path(path, follow_symlinks=False))

    def delete_file(self, path: str) -> WriteResult:
        p = self.resolve_path(path, follow_symlinks=False)
        if is_write_denied(str(p)):
            return WriteResult(error=f"Delete denied: {path} is a protected path")
        if not os.path.lexists(p):
            return WriteResult(error=f"File not found: {path}")
        if p.is_dir() and not p.is_symlink():
            return WriteResult(error=f"Path is a directory: {path}")
        try:
            p.unlink()
        except OSError as e:
            return WriteResult(error=f"Failed to delete file: {e}")
        return WriteResult()

    def move_file(self, src: str, dst: str) -> WriteResult:
        p_src = self.resolve_path(src, follow_symlinks=False)
        p_dst = self.resolve_path(dst, follow_symlinks=False)
        for p in (p_src, p_dst):
            if is_write_denied(str(p)):
                return WriteResult(error=f"Move denied: {p} is a protected path")
        if not os.path.lexists(p_src):
            return WriteResult(error=f"Source not found: {src}")
        try:
            p_dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(p_src, p_dst)
        except OSError as e:
            return WriteResult(error=f"Failed to move file: {e}")
        return WriteResult()

    def list_directory(self, path: str) -> ListResult:
        p = self.resolve_path(path)
        if not p.exists():
            return ListResult(error=f"Directory '{path}' not found.")
        if not p.is_dir():
            return ListResult(error=f"Path '{path}' is not a directory.")
        entries = []
        for child in p.iterdir():
            # 悬空符号链接或遍历期间被删除的条目跳过，不让整次列目录失败。
            try:
                st = child.stat()
            except OSError:
                continue
            is_dir = stat.S_ISDIR(st.st_mode)
            entries.append(
                {
                    "name": child.name + ("/" if is_dir else ""),
                    "is_dir": is_dir,
                    "size": st.st_size,
                    "mtime": st.st_mtime,
                },
            )
        return ListResult(entries=entries)

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
    ) -> SearchResult:
        root = self.resolve_path(path)
        if not root.exists():
            return SearchResult(error=f"Path not found: {path}")

        def shown(rel: str) -> str:
            # 与 rg 一致：结果路径以调用方给出的路径为前缀，可直接用于 read_file。
            return os.path.join(path, os.path.normpath(rel)) if root.is_dir() else path

        if target == "files":
            hits: list[tuple[float, str]] = []
            for p, rel in _iter_files(root):
                if not _glob_match(rel, pattern):
                    continue
                try:
                    hits.append((p.stat().st_mtime, shown(rel)))
                except OSError:
                    continue
            hits.sort(reverse=True)
            return SearchResult(
                files=[f for _, f in hits[offset : offset + limit]],
                total_count=len(hits),
                truncated=len(hits) > offset + limit,
            )

        try:
            regex = re.compile(pattern)
        except re.error as e:
            return SearchResult(error=f"Invalid regex: {e}")
        stop = offset + limit
        matches: list[SearchMatch] = []
        files: list[str] = []
        counts: dict[str, int] = {}
        seen = 0  # content 模式计匹配行，其他模式计命中文件
        scanned = 0
        skipped_large = 0
        hint = None
        for p, rel in _iter_files(root):
            if seen > stop:
                break
            if (file_glob and not _glob_match(rel, file_glob)) or has_binary_extension(p.name):
                continue
            if scanned >= _MAX_SEARCH_FILES:
                hint = (
                    f"Stopped after scanning {_MAX_SEARCH_FILES} files, so results may be incomplete. "
                    "Narrow the search with path or file_glob."
                )
                break
            scanned += 1
            try:
                if p.stat().st_size > MAX_EDIT_BYTES:
                    skipped_large += 1
                    continue
                data = p.read_bytes()
            except OSError:
                continue
            if b"\x00" in data[:8192]:
                continue
            lines = data.decode("utf-8", errors="replace").splitlines()
            line_hits = [i for i, line in enumerate(lines) if regex.search(line)]
            if not line_hits:
                continue
            if output_mode == "content":
                for i in line_hits:
                    if offset <= seen < stop:
                        matches.append(
                            SearchMatch(path=shown(rel), line_number=i + 1, content=_match_content(lines, i, context)),
                        )
                    seen += 1
                    if seen > stop:
                        break
            else:
                if offset <= seen < stop:
                    files.append(shown(rel))
                    counts[shown(rel)] = len(line_hits)
                seen += 1
        truncated = seen > stop
        if skipped_large:
            note = f"Skipped {skipped_large} file(s) larger than {MAX_EDIT_BYTES // (1024 * 1024)} MB."
            hint = f"{hint} {note}" if hint else note
        if output_mode == "files_only":
            return SearchResult(files=files, total_count=seen, truncated=truncated, hint=hint)
        if output_mode == "count":
            return SearchResult(counts=counts, total_count=sum(counts.values()), truncated=truncated, hint=hint)
        return SearchResult(matches=matches, total_count=seen, truncated=truncated, hint=hint)

    def _exec(self, command: str, timeout: int | None = None) -> ExecuteResult:
        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=self.env.cwd,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout or 60,
                creationflags=CREATE_NO_WINDOW,
            )
        except subprocess.TimeoutExpired as e:
            # POSIX 上超时异常携带的是未解码的 bytes。
            out = e.stdout if isinstance(e.stdout, str) else (e.stdout or b"").decode("utf-8", errors="replace")
            return ExecuteResult(stdout=out, exit_code=124)
        except OSError as e:
            return ExecuteResult(stdout=str(e), exit_code=1)
        return ExecuteResult(stdout=result.stdout, exit_code=result.returncode)

    def _has_command(self, cmd: str) -> bool:
        return shutil.which(cmd) is not None

    def _escape_shell_arg(self, arg: str) -> str:
        # Windows 上 shell=True 走 cmd.exe，单引号不是引用字符，必须用双引号规则转义。
        return subprocess.list2cmdline([arg]) if IS_WINDOWS else shlex.quote(arg)
