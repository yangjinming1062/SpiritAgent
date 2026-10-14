import os
import posixpath
import re
from typing import Any

from spirit_agent_runner.contracts.config import load_config
from spirit_agent_runner.features.files.formatting import (
    _not_utf8_error,
    _parse_search_output,
    _split_tool_diagnostics,
    _strip_bom,
    build_read_result,
    is_binary_content,
    match_existing_format,
    rank_similar_names,
    too_large_to_edit_error,
)
from spirit_agent_runner.features.files.linting import LINTERS_INPROC
from spirit_agent_runner.features.files.models import (
    BINARY_FILE_ERROR,
    DEFAULT_READ_LIMIT,
    DEFAULT_READ_OFFSET,
    DEFAULT_SEARCH_LIMIT,
    DEFAULT_SEARCH_OFFSET,
    MAX_EDIT_BYTES,
    ExecuteResult,
    ListResult,
    ReadResult,
    SearchResult,
    WriteResult,
)
from spirit_agent_runner.features.files.operations import FileOperations, TerminalEnv
from spirit_agent_runner.infrastructure.file_safety import is_write_denied


class ShellFileOperations(FileOperations):
    """远端环境：经终端环境执行 shell 命令，路径由远端 shell 按其当前目录解析。"""

    def __init__(self, env: TerminalEnv) -> None:
        super().__init__(env)
        self._command_cache: dict[str, bool] = {}

    def _exec(self, command: str, timeout: int | None = None, stdin_data: str | None = None) -> ExecuteResult:
        result = self.env.execute(command, timeout=timeout, stdin_data=stdin_data)
        return ExecuteResult(stdout=result.get("output", ""), exit_code=result.get("returncode", 0))

    def _has_command(self, cmd: str) -> bool:
        """检查命令是否在环境中可用（带缓存）。"""
        if cmd not in self._command_cache:
            result = self._exec(f"command -v {cmd} >/dev/null 2>&1 && echo 'yes'")
            self._command_cache[cmd] = result.stdout.strip() == "yes"
        return self._command_cache[cmd]

    def _escape_shell_arg(self, arg: str) -> str:
        """把字符串转义为 shell 命令可安全使用的形式。"""
        return "'" + arg.replace("'", "'\"'\"'") + "'"

    def _expand_path(self, path: str) -> str:
        """把 ``~``/``~user`` 形式的路径按远端 HOME 展开。"""
        if not path.startswith("~"):
            return path
        result = self._exec("echo $HOME")
        home = result.stdout.strip()
        if result.exit_code != 0 or not home:
            return path
        if path == "~":
            return home
        if path.startswith("~/"):
            return home + path[1:]
        rest = path[1:]
        slash_idx = rest.find("/")
        username = rest[:slash_idx] if slash_idx >= 0 else rest
        if username and re.fullmatch(r"[a-zA-Z0-9._-]+", username):
            expand_result = self._exec(f"echo ~{username}")
            if expand_result.exit_code == 0 and (user_home := expand_result.stdout.strip()):
                return user_home + path[1 + len(username) :]
        return path

    def _atomic_write(self, path: str, content: str) -> ExecuteResult:
        """同目录临时文件+rename 原子写；缺父目录则创建并标记。"""
        q_path = self._escape_shell_arg(path)
        q_parent = self._escape_shell_arg(posixpath.dirname(path) or ".")
        tmpl = self._escape_shell_arg(".spiritagent-tmp.XXXXXX")
        expected = len(content.encode("utf-8"))
        # 用 $RANDOM 不用 $$，防 PID 冲突。
        script = (
            "set -e; "
            f"d={q_parent}; t={q_path}; "
            'if [ ! -d "$d" ]; then mkdir -p "$d"; echo __SPIRITAGENT_DIRS_CREATED__; fi; '
            'tmp="$(mktemp -p "$d" ' + tmpl + " 2>/dev/null "
            '|| mktemp "$d/.spiritagent-tmp.$RANDOM.XXXXXX" 2>/dev/null '
            '|| { tmp="$d/.spiritagent-tmp.$RANDOM"; : > "$tmp" && echo "$tmp"; })"; '
            '[ -n "$tmp" ] || { echo "atomic write: could not create temp file" >&2; exit 1; }; '
            "trap 'rm -f \"$tmp\"' EXIT; "
            'if [ -e "$t" ]; then '
            'm="$(stat -c%a "$t" 2>/dev/null || stat -f%Lp "$t" 2>/dev/null || true)"; '
            "else "
            'm="$(printf %03o "$((0666 & ~0$(umask)))")"; '
            "fi; "
            '[ -n "$m" ] && chmod "$m" "$tmp" 2>/dev/null || true; '
            'cat > "$tmp"; '
            'n="$(wc -c < "$tmp" | tr -d " ")"; '
            f'[ "$n" = "{expected}" ] || {{ echo "atomic write: wrote $n of {expected} bytes" >&2; exit 1; }}; '
            'mv -f "$tmp" "$t"; '
            "trap - EXIT"
        )
        return self._exec(script, stdin_data=content)

    def _suggest_similar_files(self, path: str) -> ReadResult:
        """请求的文件未找到时推荐同目录下的相似文件。"""
        dir_path = posixpath.dirname(path) or "."
        ls_result = self._exec(f"ls -1 {self._escape_shell_arg(dir_path)} 2>/dev/null | head -50")
        names = [f for f in ls_result.stdout.strip().split("\n") if f] if ls_result.exit_code == 0 else []
        similar = [posixpath.join(dir_path, n) for n in rank_similar_names(names, posixpath.basename(path))]
        return ReadResult(error=f"File not found: {path}", similar_files=similar)

    def read_file(self, path: str, offset: int = DEFAULT_READ_OFFSET, limit: int = DEFAULT_READ_LIMIT) -> ReadResult:
        path = self._expand_path(path)
        q = self._escape_shell_arg(path)
        # awk NR 计入无结尾换行行。
        stat_result = self._exec(
            f"if [ -d {q} ]; then echo dir; elif [ -f {q} ]; then wc -c < {q}; awk 'END {{print NR}}' {q}; "
            "else exit 1; fi 2>/dev/null",
        )
        if stat_result.exit_code != 0:
            return self._suggest_similar_files(path)
        stat_lines = stat_result.stdout.split()
        if stat_lines == ["dir"]:
            return ReadResult(error=f"Path is a directory: '{path}'. Use list_directory instead.")
        try:
            file_size, total_lines = int(stat_lines[0]), int(stat_lines[1])
        except (IndexError, ValueError):
            return ReadResult(error=f"Failed to stat file: {stat_result.stdout.strip()}")
        sample = self._exec(f"head -c 1000 {q} 2>/dev/null").stdout
        if is_binary_content(path, sample):
            return ReadResult(is_binary=True, file_size=file_size, error=BINARY_FILE_ERROR)
        end_line = offset + limit - 1
        read_result = self._exec(f"sed -n '{offset},{end_line}p' {q}")
        if read_result.exit_code != 0:
            return ReadResult(error=f"Failed to read file: {read_result.stdout}")
        return build_read_result(read_result.stdout, offset, limit, total_lines, file_size)

    def read_file_raw(self, path: str) -> ReadResult:
        path = self._expand_path(path)
        q = self._escape_shell_arg(path)
        size_result = self._exec(f"if [ -f {q} ]; then wc -c < {q}; else exit 1; fi 2>/dev/null")
        if size_result.exit_code != 0:
            return self._suggest_similar_files(path)
        try:
            file_size = int(size_result.stdout.strip())
        except ValueError:
            return ReadResult(error=f"Failed to stat file: {size_result.stdout.strip()}")
        if file_size > MAX_EDIT_BYTES:
            return ReadResult(file_size=file_size, error=too_large_to_edit_error(path, file_size))
        if is_binary_content(path, self._exec(f"head -c 1000 {q} 2>/dev/null").stdout):
            return ReadResult(is_binary=True, error=BINARY_FILE_ERROR)
        cat_result = self._exec(f"cat {q}")
        if cat_result.exit_code != 0:
            return ReadResult(error=f"Failed to read file: {cat_result.stdout}")
        # 终端输出 U+FFFD 会损坏非 UTF-8 文件。
        if "\ufffd" in cat_result.stdout:
            return ReadResult(error=_not_utf8_error(path))
        content, _ = _strip_bom(cat_result.stdout)
        return ReadResult(content=content, file_size=file_size)

    def write_file(self, path: str, content: str) -> WriteResult:
        path = self._expand_path(path)
        if is_write_denied(path, config=load_config()):
            return WriteResult(error=f"Write denied: '{path}' is a protected system/credential file.")
        q = self._escape_shell_arg(path)
        probe = self._exec(f"if [ -d {q} ]; then echo dir; elif [ -f {q} ]; then wc -c < {q}; fi 2>/dev/null")
        kind = probe.stdout.strip()
        # 拒目录，防 mv 误搬临时文件。
        if kind == "dir":
            return WriteResult(error=f"Path is a directory: '{path}'. Use a file path, not a directory.")
        pre_content: str | None = None
        existing: str | None = None
        if kind.isdigit():
            # 语法检查需完整写前内容；其余只取开头判换行/BOM。
            full = os.path.splitext(path)[1].lower() in LINTERS_INPROC and int(kind) <= MAX_EDIT_BYTES
            head = self._exec(f"cat {q} 2>/dev/null" if full else f"head -c 4096 {q} 2>/dev/null")
            if head.exit_code == 0:
                existing = head.stdout
                pre_content = head.stdout if full else None
        content = match_existing_format(content, existing)
        write_result = self._atomic_write(path, content)
        if write_result.exit_code != 0:
            return WriteResult(error=f"Failed to write file: {write_result.stdout.strip()}")
        lint_result = self._check_lint_delta(path, pre_content, content)
        return WriteResult(
            bytes_written=len(content.encode("utf-8")),
            dirs_created="__SPIRITAGENT_DIRS_CREATED__" in write_result.stdout,
            lint=lint_result.to_dict(),
        )

    def exists(self, path: str) -> bool:
        return self._exec(f"test -e {self._escape_shell_arg(self._expand_path(path))}").exit_code == 0

    def delete_file(self, path: str) -> WriteResult:
        path = self._expand_path(path)
        if is_write_denied(path, config=load_config()):
            return WriteResult(error=f"Delete denied: {path} is a protected path")
        q = self._escape_shell_arg(path)
        result = self._exec(f"if [ -d {q} ] && [ ! -L {q} ]; then echo 'is a directory' >&2; exit 2; fi; rm -f -- {q}")
        if result.exit_code != 0:
            return WriteResult(error=f"Failed to delete {path}: {result.stdout.strip() or 'unknown error'}")
        return WriteResult()

    def move_file(self, src: str, dst: str) -> WriteResult:
        src = self._expand_path(src)
        dst = self._expand_path(dst)
        for p in (src, dst):
            if is_write_denied(p, config=load_config()):
                return WriteResult(error=f"Move denied: {p} is a protected path")
        q_dst = self._escape_shell_arg(dst)
        result = self._exec(
            f'mkdir -p -- "$(dirname -- {q_dst})" && mv -- {self._escape_shell_arg(src)} {q_dst}',
        )
        if result.exit_code != 0:
            return WriteResult(error=f"Failed to move {src} -> {dst}: {result.stdout.strip()}")
        return WriteResult()

    def list_directory(self, path: str) -> ListResult:
        path = self._expand_path(path)
        q = self._escape_shell_arg(path)
        # BSD find 无 -printf 时只列名字。
        result = self._exec(
            f"if [ ! -e {q} ]; then echo __missing__; elif [ ! -d {q} ]; then echo __notdir__; "
            f"elif find {q} -maxdepth 0 -printf '' >/dev/null 2>&1; then "
            f"find {q} -mindepth 1 -maxdepth 1 -printf '%Y\\t%s\\t%T@\\t%f\\n' 2>/dev/null; "
            f"else ls -1Ap {q}; fi",
            timeout=60,
        )
        if result.exit_code != 0:
            return ListResult(
                error=f"Failed to list directory '{path}': {result.stdout.strip() or f'exit code {result.exit_code}'}",
            )
        out = result.stdout.strip()
        if out == "__missing__":
            return ListResult(error=f"Directory '{path}' not found.")
        if out == "__notdir__":
            return ListResult(error=f"Path '{path}' is not a directory.")
        entries: list[dict[str, Any]] = []
        for line in out.split("\n"):
            if not line:
                continue
            parts = line.split("\t", 3)
            if len(parts) == 4:
                kind, size, mtime, name = parts
                is_dir = kind == "d"
                try:
                    entry_size, entry_mtime = int(size), float(mtime)
                except ValueError:
                    continue
                entries.append(
                    {
                        "name": name + ("/" if is_dir else ""),
                        "is_dir": is_dir,
                        "size": entry_size,
                        "mtime": entry_mtime,
                    },
                )
            else:
                entries.append({"name": line, "is_dir": line.endswith("/")})
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
        path = self._expand_path(path)
        if self._exec(f"test -e {self._escape_shell_arg(path)}").exit_code != 0:
            parent = posixpath.dirname(path) or "."
            hint = f"Path not found: {path}"
            ls_result = self._exec(f"ls -1 {self._escape_shell_arg(parent)} 2>/dev/null | head -50")
            names = [n for n in ls_result.stdout.strip().split("\n") if n] if ls_result.exit_code == 0 else []
            if similar := rank_similar_names(names, posixpath.basename(path)):
                hint += ". Similar paths: " + ", ".join(posixpath.join(parent, n) for n in similar)
            return SearchResult(error=hint)
        if target == "files":
            return self._search_files(pattern, path, limit, offset)
        if self._has_command("rg"):
            return self._search_content_rg(pattern, path, file_glob, limit, offset, output_mode, context)
        if self._has_command("grep"):
            return self._search_content_grep(pattern, path, file_glob, limit, offset, output_mode, context)
        return SearchResult(
            error="Content search requires ripgrep (rg) or grep in the terminal environment.",
        )

    def _search_files(self, pattern: str, path: str, limit: int, offset: int) -> SearchResult:
        """按文件名 glob 搜索，按修改时间倒序；跳过搜索根下的隐藏路径。"""
        q_path = self._escape_shell_arg(path)
        # 多取一条以判截断。
        fetch = limit + offset + 1
        if self._has_command("rg"):
            glob_pattern = pattern if "/" in pattern or pattern.startswith("*") else f"*{pattern}"
            q_glob = self._escape_shell_arg(glob_pattern)
            result = self._exec(
                f"rg --files --sortr=modified -g {q_glob} {q_path} 2>/dev/null | head -n {fetch}",
                timeout=60,
            )
            if result.exit_code not in (0, 1, 141):
                return SearchResult(error=f"File search failed: {result.stdout.strip() or result.exit_code}")
            if not result.stdout.strip():
                result = self._exec(f"rg --files -g {q_glob} {q_path} 2>/dev/null | head -n {fetch}", timeout=60)
            if result.exit_code not in (0, 1, 141):
                return SearchResult(error=f"File search failed: {result.stdout.strip() or result.exit_code}")
            all_files = [f for f in result.stdout.strip().split("\n") if f]
        elif self._has_command("find"):
            root = path.rstrip("/") or "/"
            hidden = " ".join(
                f"-not -path {self._escape_shell_arg(posixpath.join(root, sub))}" for sub in (".*", "*/.*")
            )
            # find -name 只比文件名。
            name_pattern = self._escape_shell_arg(pattern.rsplit("/", 1)[-1])
            find_cmd = f"find {q_path} {hidden} -type f -name {name_pattern}"
            result = self._exec(
                f"{find_cmd} -printf '%T@ %p\\n' 2>/dev/null | sort -rn | cut -d' ' -f2- | head -n {fetch}",
                timeout=60,
            )
            if result.exit_code not in (0, 1, 141):
                return SearchResult(error=f"File search failed: {result.stdout.strip() or result.exit_code}")
            if not result.stdout.strip():
                # BSD 退化为不按时间排序。
                result = self._exec(f"{find_cmd} 2>/dev/null | head -n {fetch}", timeout=60)
            if result.exit_code not in (0, 1, 141):
                return SearchResult(error=f"File search failed: {result.stdout.strip() or result.exit_code}")
            all_files = [f for f in result.stdout.strip().split("\n") if f]
        else:
            return SearchResult(error="File search requires 'rg' (ripgrep) or 'find' in the terminal environment.")
        truncated = len(all_files) > limit + offset
        return SearchResult(
            files=all_files[offset : offset + limit],
            total_count=min(len(all_files), limit + offset),
            truncated=truncated,
        )

    def _search_content_rg(
        self,
        pattern: str,
        path: str,
        file_glob: str | None,
        limit: int,
        offset: int,
        output_mode: str,
        context: int,
    ) -> SearchResult:
        cmd_parts = ["rg", "--line-number", "--no-heading", "--with-filename"]
        if context > 0:
            cmd_parts.extend(["-C", str(context)])
        if file_glob:
            cmd_parts.extend(["--glob", self._escape_shell_arg(file_glob)])
        return self._run_content_search(cmd_parts, pattern, path, limit, offset, output_mode, context)

    def _search_content_grep(
        self,
        pattern: str,
        path: str,
        file_glob: str | None,
        limit: int,
        offset: int,
        output_mode: str,
        context: int,
    ) -> SearchResult:
        # 跳过隐藏项；目录模式不用 '.*'（GNU grep 会误伤）。
        cmd_parts = ["grep", "-rnHE", "--exclude-dir='.[!.]*'", "--exclude-dir='..?*'", "--exclude='.*'"]
        if context > 0:
            cmd_parts.extend(["-C", str(context)])
        if file_glob:
            cmd_parts.extend(["--include", self._escape_shell_arg(file_glob)])
        return self._run_content_search(cmd_parts, pattern, path, limit, offset, output_mode, context)

    def _run_content_search(
        self,
        cmd_parts: list[str],
        pattern: str,
        path: str,
        limit: int,
        offset: int,
        output_mode: str,
        context: int,
    ) -> SearchResult:
        if output_mode == "files_only":
            cmd_parts.append("-l")
        elif output_mode == "count":
            cmd_parts.append("-c")
        # 模式以 - 开头须防当成选项。
        cmd_parts.extend(["-e", self._escape_shell_arg(pattern), "--", self._escape_shell_arg(path)])
        # 多取余量以判截断。
        fetch_limit = limit + offset + 1 + (200 if context > 0 else 0)
        result = self._exec(f"set -o pipefail; {' '.join(cmd_parts)} | head -n {fetch_limit}", timeout=60)
        diagnostics, payload = _split_tool_diagnostics(result.stdout)
        if result.exit_code not in (0, 1, 2, 141) or (result.exit_code == 2 and not payload.strip()):
            return SearchResult(
                error=f"Search failed: {diagnostics.strip() or result.stdout.strip() or f'exit code {result.exit_code}'}",
            )
        return _parse_search_output(payload, output_mode, limit, offset, context)
