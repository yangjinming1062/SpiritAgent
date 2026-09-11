import contextlib
import difflib
import fnmatch
import logging
import os
import re
import shlex
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from utils import is_write_denied, strip_ansi

from .fuzzy_match import format_no_match_hint, fuzzy_find_and_replace
from .helpers import (
    _UTF8_BOM,
    IMAGE_EXTENSIONS,
    LINTERS,
    LINTERS_INPROC,
    MAX_FILE_SIZE,
    ExecuteResult,
    FileOperations,
    LintResult,
    PatchResult,
    ReadResult,
    SearchMatch,
    SearchResult,
    WriteResult,
    _detect_line_ending,
    _has_bom,
    _looks_like_linter_unusable,
    _normalize_line_endings,
    _strip_bom,
    get_max_line_length,
    normalize_read_pagination,
)

logger = logging.getLogger(__name__)


class NativeFileOperations(FileOperations):
    """基于原生 pathlib/os 的文件操作实现。

    避免走 shell，在 Windows 本地环境下鲁棒性更好。
    """

    def __init__(self, cwd: str | None = None) -> None:
        self.cwd = cwd or os.getcwd()

    def _expand_path(self, path: str) -> Path:
        p = Path(os.path.expanduser(path))
        if not p.is_absolute():
            p = Path(self.cwd) / p
        return p.resolve()

    def _add_line_numbers(self, text: str, start_line: int) -> str:
        if not text:
            return text
        max_len = get_max_line_length()
        lines = text.split("\n")
        numbered = []
        for i, line in enumerate(lines, start=start_line):
            if len(line) > max_len:
                line = line[:max_len] + "... [truncated]"
            numbered.append(f"{i}|{line}")
        return "\n".join(numbered)

    def _is_image(self, path: Path) -> bool:
        return path.suffix.lower() in IMAGE_EXTENSIONS

    def _is_likely_binary(self, content_sample: bytes) -> bool:
        if not content_sample:
            return False
        if b"\x00" in content_sample:
            return True
        non_printable = sum(1 for b in content_sample if b < 32 and b not in b"\n\r\t")
        return non_printable / len(content_sample) > 0.30

    def read_file(self, path: str, offset: int = 1, limit: int = 500) -> ReadResult:
        p = self._expand_path(path)
        offset, limit = normalize_read_pagination(offset, limit)

        if not p.exists():
            return ReadResult(error=f"File not found: '{path}'")
        if p.is_dir():
            return ReadResult(error=f"Path is a directory: '{path}'. Use list_directory instead.")

        try:
            file_size = p.stat().st_size
        except OSError as e:
            return ReadResult(error=f"Error accessing file: {e}")

        if file_size > MAX_FILE_SIZE and limit >= 500:
            return ReadResult(
                file_size=file_size,
                error=(
                    f"File size {file_size:,} bytes exceeds safety cap of {MAX_FILE_SIZE:,} bytes. Read with offset/limit."
                ),
            )

        if self._is_image(p):
            return ReadResult(
                is_image=True,
                is_binary=True,
                file_size=file_size,
                hint="Image file detected. Automatically redirected to vision_analyze tool.",
            )

        try:
            with p.open("rb") as f:
                sample = f.read(1000)
            if self._is_likely_binary(sample):
                return ReadResult(is_binary=True, file_size=file_size, error="Binary file - cannot display as text.")
        except OSError as e:
            return ReadResult(error=f"Error reading file: {e}")

        total_lines = 0
        content_lines = []
        try:
            with p.open("r", encoding="utf-8", errors="replace") as f:
                for i, line in enumerate(f, start=1):
                    total_lines += 1
                    if offset <= i < offset + limit:
                        content_lines.append(line)
        except OSError as e:
            return ReadResult(error=f"Error reading file: {e}")

        end_line = offset + limit - 1
        read_output = "".join(content_lines).rstrip("\r\n")

        if offset == 1:
            read_output, _ = _strip_bom(read_output)

        truncated = total_lines > end_line
        hint = None
        if truncated:
            hint = f"Use offset={end_line + 1} to continue reading (showing {offset}-{end_line} of {total_lines} lines)"

        return ReadResult(
            content=self._add_line_numbers(read_output, offset),
            total_lines=total_lines,
            file_size=file_size,
            truncated=truncated,
            hint=hint,
        )

    def read_file_raw(self, path: str) -> ReadResult:
        p = self._expand_path(path)
        if not p.exists():
            return ReadResult(error=f"File not found: '{path}'")
        try:
            file_size = p.stat().st_size
            if file_size > MAX_FILE_SIZE:
                return ReadResult(
                    file_size=file_size,
                    error=f"File size {file_size:,} bytes exceeds safety cap of {MAX_FILE_SIZE:,} bytes. Use read_file with offset/limit instead.",
                )
            content = p.read_text(encoding="utf-8", errors="replace")
            return ReadResult(content=content, total_lines=len(content.splitlines()), file_size=file_size)
        except Exception as e:
            return ReadResult(error=f"Failed to read file: {e}")

    def write_file(self, path: str, content: str) -> WriteResult:
        p = self._expand_path(path)
        if is_write_denied(str(p)):
            return WriteResult(error=f"Write denied: '{path}' is a protected system/credential file.")
        # 拒绝目录 — 不然 Path.write_text 会抛 PermissionError 而非清晰报错。
        if p.is_dir():
            return WriteResult(error=f"Path is a directory: '{path}'. Use a file path, not a directory.")

        pre_content = None
        if p.exists():
            with contextlib.suppress(Exception):
                pre_content = p.read_text(encoding="utf-8", errors="replace")

        original_ending = _detect_line_ending(pre_content) if pre_content else None
        if original_ending == "\r\n":
            content = _normalize_line_endings(content, "\r\n")

        if pre_content and _has_bom(pre_content) and not _has_bom(content):
            content = _UTF8_BOM + content

        dirs_created = False
        try:
            if not p.parent.exists():
                p.parent.mkdir(parents=True, exist_ok=True)
                dirs_created = True

            p.write_text(content, encoding="utf-8")
            bytes_written = len(content.encode("utf-8"))

            lint_result = self._check_lint_delta(str(p), pre_content=pre_content, post_content=content)

            return WriteResult(
                bytes_written=bytes_written,
                dirs_created=dirs_created,
                lint=lint_result.to_dict() if lint_result else None,
            )
        except Exception as e:
            return WriteResult(error=f"Failed to write file: {e}", dirs_created=dirs_created)

    def patch_replace(self, path: str, old_string: str, new_string: str, replace_all: bool = False) -> PatchResult:
        p = self._expand_path(path)
        if is_write_denied(str(p)):
            return PatchResult(success=False, error=f"Write denied: '{path}' is a protected system/credential file.")

        if not p.exists():
            return PatchResult(success=False, error=f"File not found: {path}")

        try:
            file_size = p.stat().st_size
            if file_size > MAX_FILE_SIZE:
                return PatchResult(
                    success=False,
                    error=f"File size {file_size:,} bytes exceeds safety cap of {MAX_FILE_SIZE:,} bytes. Cannot patch.",
                )
        except OSError:
            pass

        try:
            content = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return PatchResult(success=False, error="Cannot patch binary file")
        except Exception as e:
            return PatchResult(success=False, error=f"Error reading file: {e}")

        occurrences = content.count(old_string)
        if occurrences == 0:
            new_content, match_count, _strategy, error = fuzzy_find_and_replace(
                content,
                old_string,
                new_string,
                replace_all,
            )
            if error or match_count == 0:
                err_msg = error or f"Could not find match for old_string in {path}"
                with contextlib.suppress(Exception):
                    err_msg += format_no_match_hint(err_msg, match_count, old_string, content)
                return PatchResult(success=False, error=err_msg)
            content_after = new_content
        else:
            if occurrences > 1 and not replace_all:
                return PatchResult(
                    success=False,
                    error=f"Found {occurrences} occurrences. Use replace_all=True if intentional.",
                )
            content_after = content.replace(old_string, new_string, -1 if replace_all else 1)

        file_ending = _detect_line_ending(content)
        if file_ending:
            content_after = _normalize_line_endings(content_after, file_ending)

        try:
            p.write_text(content_after, encoding="utf-8")
        except Exception as e:
            return PatchResult(success=False, error=f"Error writing file: {e}")

        # 写后再读一次确认内容真的落地（防 Windows 杀软拦截、磁盘满、半写）。比对前
        # 统一换行符（不同 Python 版本在 read_text 上对 \r\n 处理有差异）。
        try:
            verified = p.read_text(encoding="utf-8")
            _v = verified.replace("\r\n", "\n").replace("\r", "\n")
            _w = content_after.replace("\r\n", "\n").replace("\r", "\n")
            if _v != _w:
                return PatchResult(
                    success=False,
                    error=(
                        f"Post-write verification failed for {path}: on-disk content "
                        f"differs from intended write (wrote {len(_w)} chars, read back "
                        f"{len(_v)} chars after normalizing line endings). "
                        "The patch did not persist. Re-read the file and try again."
                    ),
                )
        except Exception as e:
            return PatchResult(success=False, error=f"Post-write verification read failed for {path}: {e}")

        old_lines = content.splitlines(keepends=True)
        new_lines = content_after.splitlines(keepends=True)
        diff = "".join(difflib.unified_diff(old_lines, new_lines, fromfile=f"a/{path}", tofile=f"b/{path}"))

        # 补丁后再跑一次 lint 检查
        lint_result = self._check_lint_delta(str(p), pre_content=content, post_content=content_after)

        return PatchResult(
            success=True,
            diff=diff,
            files_modified=[path],
            lint=lint_result.to_dict() if lint_result else None,
        )

    def patch_v4a(self, patch_content: str) -> PatchResult:
        return PatchResult(
            success=False,
            error="Unified diff patch application is not supported in native Windows ops yet.",
        )

    def delete_file(self, path: str) -> WriteResult:
        p = self._expand_path(path)
        if is_write_denied(str(p)):
            return WriteResult(error=f"Delete denied: {path} is a protected path")
        if not p.exists():
            return WriteResult(error=f"File not found: {path}")
        if p.is_dir() and not p.is_symlink():
            return WriteResult(error=f"Path is a directory: {path}")
        try:
            p.unlink()
            return WriteResult(bytes_written=0)
        except Exception as e:
            return WriteResult(error=f"Failed to delete file: {e}")

    def delete_path(self, path: str, recursive: bool = False) -> WriteResult:
        p = self._expand_path(path)
        if is_write_denied(str(p)):
            return WriteResult(error=f"Delete denied: {path} is a protected path")
        if not p.exists():
            return WriteResult(error=f"Path not found: {path}")
        try:
            if p.is_dir() and not p.is_symlink():
                if recursive:
                    shutil.rmtree(str(p))
                else:
                    p.rmdir()
            else:
                p.unlink()
            return WriteResult(bytes_written=0)
        except Exception as e:
            return WriteResult(error=f"Failed to delete path: {e}")

    def move_file(self, src: str, dst: str) -> WriteResult:
        p_src = self._expand_path(src)
        p_dst = self._expand_path(dst)
        for p in (p_src, p_dst):
            if is_write_denied(str(p)):
                return WriteResult(error=f"Move denied: {p} is a protected path")
        if not p_src.exists():
            return WriteResult(error=f"Source not found: {src}")
        try:
            if not p_dst.parent.exists():
                p_dst.parent.mkdir(parents=True, exist_ok=True)
            p_src.rename(p_dst)
            return WriteResult(bytes_written=0)
        except Exception as e:
            return WriteResult(error=f"Failed to move file: {e}")

    def search(
        self,
        pattern: str,
        path: str = ".",
        target: str = "content",
        file_glob: str | None = None,
        limit: int = 50,
        offset: int = 0,
        output_mode: str = "content",
        context: int = 0,
    ) -> SearchResult:
        if target == "files":
            return self._search_files_by_name(pattern, path, limit, offset)
        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            return SearchResult(error=f"Invalid regex: {e}")

        search_root = self._expand_path(path)
        if not search_root.exists():
            return SearchResult(error=f"Search path not found: {path}")

        matches = []
        files = set()
        counts = {}
        total_count = 0
        truncated = False

        def get_files() -> "Iterator[Path]":
            if not search_root.is_dir():
                yield search_root
                return
            if file_glob:
                yield from search_root.rglob(file_glob)
            else:
                yield from search_root.rglob("*")

        def should_skip(p: Path) -> bool:
            # 相对搜索根判定：若根本身位于某个点目录下，不应据此屏蔽根下所有文件。
            return any(part.startswith(".") and len(part) > 1 for part in p.relative_to(search_root).parts[:-1])

        scanned_count = 0
        for p in get_files():
            if not p.is_file() or should_skip(p):
                continue

            rel_path = str(p.relative_to(search_root)) if search_root.is_dir() else p.name

            try:
                content = p.read_text(encoding="utf-8", errors="replace")
                lines = content.splitlines()
                mtime = p.stat().st_mtime

                file_match_count = 0
                for i, line in enumerate(lines):
                    if regex.search(line):
                        if total_count >= offset and len(matches) < limit:
                            start_ctx = max(0, i - context)
                            end_ctx = min(len(lines), i + context + 1)
                            match_content = "\n".join(
                                f"{j + 1}{':' if j == i else '-'}{lines[j]}" for j in range(start_ctx, end_ctx)
                            )
                            matches.append(
                                SearchMatch(path=rel_path, line_number=i + 1, content=match_content, mtime=mtime),
                            )
                            files.add(rel_path)
                            file_match_count += 1

                        total_count += 1
                        if total_count > offset + limit:
                            truncated = True
                            break

                if file_match_count > 0:
                    counts[rel_path] = file_match_count

            except (UnicodeDecodeError, OSError) as e:
                logger.debug("search skipped unreadable file %s: %s", rel_path, e)
                continue

            scanned_count += 1
            if truncated or scanned_count > 1000:
                truncated = True
                break

        return SearchResult(
            matches=matches,
            files=list(files),
            counts=counts,
            total_count=total_count,
            truncated=truncated,
        )

    def _search_files_by_name(self, pattern: str, path: str, limit: int, offset: int) -> SearchResult:
        search_root = self._expand_path(path)
        if not search_root.exists():
            return SearchResult(error=f"Search path not found: {path}")

        def should_skip(p: Path) -> bool:
            return any(part.startswith(".") and len(part) > 1 for part in p.relative_to(search_root).parts[:-1])

        candidates: Iterator[Path] = iter([search_root]) if not search_root.is_dir() else search_root.rglob("*")
        hits: list[tuple[float, str]] = []
        for p in candidates:
            if not p.is_file() or should_skip(p):
                continue
            rel_path = str(p.relative_to(search_root)) if search_root.is_dir() else p.name
            if not fnmatch.fnmatch(rel_path, pattern):
                continue
            try:
                mtime = p.stat().st_mtime
            except OSError:
                logger.debug("search skipped unstatable file %s", rel_path)
                continue
            hits.append((mtime, rel_path))
        hits.sort(reverse=True)
        page = [rel for _, rel in hits[offset : offset + limit]]
        return SearchResult(files=page, total_count=len(hits), truncated=len(hits) > offset + limit)

    def _exec(self, command: str, cwd: str | None = None, timeout: int = 60, stdin_data: str | None = None) -> Any:
        kwargs = {"shell": True, "text": True, "capture_output": True, "timeout": timeout}
        if stdin_data is not None:
            kwargs["input"] = stdin_data
        try:
            result = subprocess.run(command, cwd=cwd or self.cwd, **kwargs)
            return ExecuteResult(stdout=result.stdout, exit_code=result.returncode)
        except subprocess.TimeoutExpired as e:
            # text=True 模式下 TimeoutExpired.stdout 已为 str|None，再次解码会抛错。
            return ExecuteResult(stdout=e.stdout or "", exit_code=124)
        except Exception as e:
            return ExecuteResult(stdout=str(e), exit_code=1)

    def _has_command(self, cmd: str) -> bool:
        return shutil.which(cmd) is not None

    def _escape_shell_arg(self, arg: str) -> str:
        return shlex.quote(arg)

    def _check_lint(self, path: str, content: str | None = None) -> Any:
        ext = os.path.splitext(path)[1].lower()
        inproc = LINTERS_INPROC.get(ext)
        if inproc is not None:
            if content is None:
                try:
                    content = Path(path).read_text(encoding="utf-8", errors="replace")
                except Exception as e:
                    return LintResult(skipped=True, message=f"Failed to read {path} for lint: {e}")
            ok, err = inproc(content)
            return LintResult(success=ok, output="" if ok else err)
        if ext not in LINTERS:
            return LintResult(skipped=True, message=f"No linter for {ext} files")
        linter_cmd = LINTERS[ext]
        base_cmd = linter_cmd.split()[0]
        if not self._has_command(base_cmd):
            return LintResult(skipped=True, message=f"{base_cmd} not available")
        cmd = linter_cmd.replace("{file}", self._escape_shell_arg(path))
        result = self._exec(cmd, timeout=30)
        if result.exit_code != 0 and _looks_like_linter_unusable(base_cmd, result.stdout):
            cleaned = strip_ansi(result.stdout).strip()
            first_line = next((ln.strip() for ln in cleaned.splitlines() if ln.strip()), cleaned[:120])
            return LintResult(skipped=True, message=f"{base_cmd} not usable: {first_line[:200]}")
        return LintResult(success=result.exit_code == 0, output=result.stdout.strip() if result.stdout.strip() else "")

    def _check_lint_delta(self, path: str, pre_content: str | None, post_content: str | None = None) -> Any:
        post = self._check_lint(path, content=post_content)
        if post.success or post.skipped:
            return post
        if pre_content is None:
            return post
        pre = self._check_lint(path, content=pre_content)
        if pre.success or pre.skipped or not pre.output:
            return post
        pre_lines = {ln.strip() for ln in pre.output.splitlines() if ln.strip()}
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
