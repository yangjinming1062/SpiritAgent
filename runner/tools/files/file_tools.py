import errno
import json
import logging
import os
import posixpath
import sys
import threading
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from typing import Any

from envs import register_env_cleanup_hook, resolve_container_task_id, use_environment
from utils import (
    IS_WINDOWS,
    get_read_block_error,
    get_spiritagent_home,
    get_windows_sensitive_prefixes,
    has_traversal_component,
    load_config,
    redact_sensitive_text,
)

from ..registry import registry, tool_error
from ..tool_output_limits import DEFAULT_MAX_LINES
from .binary_extensions import has_binary_extension
from .helpers import (
    DEFAULT_READ_LIMIT,
    FileOperations,
    OperationType,
    PatchOperation,
    PatchResult,
    ShellFileOperations,
    apply_v4a_operations,
    check_stale,
    lock_path,
    normalize_read_pagination,
    normalize_search_pagination,
    note_write,
    parse_v4a_patch,
    record_read,
)
from .native_ops import NativeFileOperations

logger = logging.getLogger(__name__)

_EXPECTED_WRITE_ERRNOS = {errno.EACCES, errno.EPERM, errno.EROFS}

# 以字符近似 token（100K≈25–35K）；可由 file_read_max_chars 覆盖。
_DEFAULT_MAX_READ_CHARS = 100_000
_max_read_chars_cached: int | None = None


def _get_max_read_chars() -> int:
    """返回单次读取允许的最大字符数（进程内缓存，配置更新时由 reset_max_read_chars_cache 失效）。"""
    global _max_read_chars_cached
    if _max_read_chars_cached is None:
        val = load_config().get("file_read_max_chars")
        _max_read_chars_cached = int(val) if isinstance(val, int | float) and val > 0 else _DEFAULT_MAX_READ_CHARS
    return _max_read_chars_cached


def reset_max_read_chars_cache() -> None:
    """清除 file_read_max_chars 缓存，下次调用重新读配置。"""
    global _max_read_chars_cached
    _max_read_chars_cached = None


# 超阈值且未给窄范围时提示分段读。
_LARGE_FILE_HINT_BYTES = 512_000

# 拒绝无限输出/阻塞的设备路径。
_BLOCKED_DEVICE_PATHS = frozenset(
    {
        "/dev/zero",
        "/dev/random",
        "/dev/urandom",
        "/dev/full",
        "/dev/stdin",
        "/dev/tty",
        "/dev/console",
        "/dev/stdout",
        "/dev/stderr",
        "/dev/fd/0",
        "/dev/fd/1",
        "/dev/fd/2",
    },
)


def _is_blocked_device_path(path: str) -> bool:
    """设备/stdio 路径会让读取挂起；/proc 下的 environ、cmdline、maps 会泄露进程凭据与内存布局。"""
    if path in _BLOCKED_DEVICE_PATHS:
        return True
    if not path.startswith("/proc/"):
        return False
    return path.endswith(("/fd/0", "/fd/1", "/fd/2", "/environ", "/cmdline", "/maps"))


# 写禁区按解析路径前缀匹配（尾部 / 防误伤）；比较前统一小写。
_CASE_INSENSITIVE_PATHS = IS_WINDOWS or sys.platform == "darwin"
_SENSITIVE_PATH_PREFIXES = (
    # Linux 系统与 systemd 状态（远端环境）
    "/etc/",
    "/boot/",
    "/usr/lib/systemd/",
    "/var/lib/systemd/",
    # macOS 系统目录与本地目录服务（DSLocal 含密码哈希）
    "/private/etc/",
    "/private/var/",
    "/system/",
    "/library/apple/usr/libexec/oah/",
    *get_windows_sensitive_prefixes(),
)
_SENSITIVE_EXACT_PATHS = frozenset(
    {"/var/run/docker.sock", "/run/docker.sock"} | ({"c:/pagefile.sys", "c:/hiberfil.sys"} if IS_WINDOWS else set()),
)


def _compare_form(path: str) -> str:
    """转为与敏感路径表比较的形式：正斜杠，大小写不敏感的平台上小写。"""
    path = path.replace("\\", "/") if IS_WINDOWS else path
    return path.lower() if _CASE_INSENSITIVE_PATHS else path


def _user_sensitive_paths() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """当前用户 home 下的凭据位置（前缀, 精确路径）；锚定到 home，避免误拒工作区里同名的子目录。"""
    if IS_WINDOWS:
        home_vars = ("USERPROFILE", "HOME")
        subdirs = ("appdata/roaming/microsoft/", "appdata/local/microsoft/")
        files = ("ntuser.dat", "ntuser.dat.log", "ntuser.ini")
    elif sys.platform == "darwin":
        # 钥匙串、TCC 与 cookie。
        home_vars = ("HOME",)
        subdirs = ("library/keychains/", "library/application support/com.apple.tcc/", "library/cookies/")
        files = ()
    else:
        return (), ()
    homes = {_compare_form(v).rstrip("/") + "/" for var in home_vars if (v := os.environ.get(var))}
    return tuple(h + s for h in homes for s in subdirs), tuple(h + f for h in homes for f in files)


_SENSITIVE_USER_PREFIXES, _SENSITIVE_USER_EXACTS = _user_sensitive_paths()


def _sensitive_write_error(target: str, local: bool) -> str | None:
    """写入目标在系统/凭据/设置区时返回拒绝原因；远端只比字面路径。"""
    normalized = _compare_form(target if local else posixpath.normpath(target))
    if (
        normalized.startswith(_SENSITIVE_PATH_PREFIXES)
        or normalized.startswith(_SENSITIVE_USER_PREFIXES)
        or normalized in _SENSITIVE_EXACT_PATHS
        or normalized in _SENSITIVE_USER_EXACTS
    ):
        return (
            f"Refusing to write to sensitive system path: {target}. "
            "File tools cannot modify system locations. If the user really needs this change, explain the risk and "
            "ask them before changing it any other way."
        )
    # 设置含安全配置，禁止模型改写。
    if local and normalized == _compare_form(str((get_spiritagent_home() / "desktop-settings.json").resolve())):
        return (
            f"Refusing to write to the app settings file: {target}. Change settings in the app's settings page instead."
        )
    return None


def _is_expected_write_exception(exc: Exception) -> bool:
    """返回 True 表示是可预期的写入拒绝（不应记入 error 日志）。"""
    if isinstance(exc, PermissionError):
        return True
    return bool(isinstance(exc, OSError) and exc.errno in _EXPECTED_WRITE_ERRNOS)


_file_ops_lock = threading.Lock()
_file_ops_cache: dict[str, FileOperations] = {}


@contextmanager
def _file_ops(task_id: str) -> Iterator[FileOperations]:
    """在 with 块内返回任务终端环境对应的文件操作实例并持有该环境：local 环境用原生 I/O，其他环境经 shell 执行。"""
    task_id = resolve_container_task_id(task_id)
    with use_environment(task_id) as env:
        with _file_ops_lock:
            # 缓存仅绑定同一环境对象时复用。
            file_ops = _file_ops_cache.get(task_id)
            if file_ops is None or file_ops.env is not env:
                file_ops = NativeFileOperations(env) if env.env_type == "local" else ShellFileOperations(env)
                _file_ops_cache[task_id] = file_ops
        yield file_ops


def clear_file_ops_cache(task_id: str | None = None) -> None:
    """清空文件操作缓存（环境清理钩子）。"""
    with _file_ops_lock:
        if task_id:
            _file_ops_cache.pop(task_id, None)
        else:
            _file_ops_cache.clear()


register_env_cleanup_hook(clear_file_ops_cache)


def _target(file_ops: FileOperations, path: str, *, follow_symlinks: bool = True) -> str:
    """交给文件操作的路径：本地为按终端当前目录解析的绝对路径，远端保持原样由远端 shell 解析。"""
    if isinstance(file_ops, NativeFileOperations):
        return str(file_ops.resolve_path(path, follow_symlinks=follow_symlinks))
    return path


def list_directory_tool(path: str, task_id: str = "default") -> str:
    """列出目录内容。"""
    if not path:
        return tool_error("list_directory: missing 'path'.")
    try:
        with _file_ops(task_id) as file_ops:
            target = _target(file_ops, path)
            if block_error := get_read_block_error(target):
                return tool_error(block_error)
            result = file_ops.list_directory(target)
            if result.error:
                return tool_error(result.error)
            entries = sorted(result.entries, key=lambda x: (not x["is_dir"], x["name"]))
            # 按约 120 字符/条估算上限。
            max_entries = max(50, registry.get_max_result_size() // 120)
            output: dict[str, Any] = {"path": target, "entries": entries[:max_entries]}
            if len(entries) > max_entries:
                output["truncated"] = True
                output["hint"] = f"Directory has more than {max_entries} entries; use search_files to filter."
            return json.dumps(output, ensure_ascii=False)
    except Exception as e:
        return tool_error(str(e))


def read_file_tool(path: str, offset: int = 1, limit: int = DEFAULT_READ_LIMIT, task_id: str = "default") -> str:
    """带分页与行号读取文件。"""
    if not path:
        return tool_error("read_file: missing 'path'.")
    try:
        offset, limit = normalize_read_pagination(offset, limit)
        with _file_ops(task_id) as file_ops:
            local = isinstance(file_ops, NativeFileOperations)
            target = _target(file_ops, path)
            # 字面与解析路径都查：拦别名与指向设备的链接。
            if _is_blocked_device_path(path) or _is_blocked_device_path(target):
                return tool_error(
                    f"Cannot read '{path}': this is a device file that would block or produce infinite output.",
                )
            if has_binary_extension(target):
                return tool_error(f"Cannot read binary file '{path}': read_file only returns text.")
            if block_error := get_read_block_error(target):
                return tool_error(block_error)

            result = file_ops.read_file(target, offset, limit)
            result_dict = result.to_dict()
            # 按进入上下文的内容计字符。
            max_chars = _get_max_read_chars()
            if len(result.content) > max_chars:
                return tool_error(
                    f"Read produced {len(result.content):,} characters which exceeds the safety limit ({max_chars:,} chars). "
                    f"Use offset and limit to read a smaller range. The file has {result.total_lines} lines total.",
                    path=path,
                    total_lines=result.total_lines,
                    file_size=result.file_size,
                )
            if result.content:
                result_dict["content"] = redact_sensitive_text(result.content)
            if result.file_size > _LARGE_FILE_HINT_BYTES and limit > 200 and result.truncated:
                result_dict["_hint"] = (
                    f"This file is large ({result.file_size:,} bytes). Consider reading only the "
                    "section you need with offset and limit to keep context usage efficient."
                )
            if local and not result.error:
                record_read(task_id, target, partial=offset > 1 or result.truncated)
            return json.dumps(result_dict, ensure_ascii=False)
    except Exception as e:
        return tool_error(str(e))


def write_file_tool(path: str, content: str, task_id: str = "default") -> str:
    """把内容写入文件。"""
    try:
        with _file_ops(task_id) as file_ops:
            local = isinstance(file_ops, NativeFileOperations)
            target = _target(file_ops, path)
            if sensitive_err := _sensitive_write_error(target, local):
                return tool_error(sensitive_err)
            # 同路径读改写串行。
            with lock_path(target):
                warning = check_stale(task_id, target, whole_file=True) if local else None
                result = file_ops.write_file(target, content)
                result_dict = result.to_dict()
                if not result.error:
                    result_dict["files_modified"] = [target]
                    if local:
                        note_write(task_id, target)
            if local:
                result_dict["resolved_path"] = target
            if warning:
                result_dict["_warning"] = warning
            return json.dumps(result_dict, ensure_ascii=False)
    except Exception as e:
        if _is_expected_write_exception(e):
            logger.debug("write_file expected denial: %s: %s", type(e).__name__, e)
        else:
            logger.error("write_file error: %s: %s", type(e).__name__, e, exc_info=True)
        return tool_error(str(e))


def patch_tool(
    mode: str = "replace",
    path: str | None = None,
    old_string: str | None = None,
    new_string: str | None = None,
    replace_all: bool = False,
    patch: str | None = None,
    task_id: str = "default",
) -> str:
    """以 replace 模式或 V4A 补丁格式修改文件。"""
    try:
        with _file_ops(task_id) as file_ops:
            local = isinstance(file_ops, NativeFileOperations)
            targets: list[str] = []
            operations: list[PatchOperation] = []
            if mode == "replace":
                if not path:
                    return tool_error("patch: 'path' is required when mode='replace'.")
                if old_string is None or new_string is None:
                    return tool_error("patch: 'old_string' and 'new_string' are required when mode='replace'.")
                targets.append(_target(file_ops, path))
            elif mode == "patch":
                if not patch:
                    return tool_error("patch: 'patch' content is required when mode='patch'.")
                operations, parse_error = parse_v4a_patch(patch)
                if parse_error:
                    return tool_error(f"Failed to parse patch: {parse_error}")
                for op in operations:
                    # 补丁头部路径拒 .. 穿越；显式 path 不受此限。
                    for raw in (op.file_path, op.new_path):
                        if raw and has_traversal_component(raw):
                            return tool_error(
                                f"V4A patch header contains '..' traversal: {raw!r}. "
                                "Use a path relative to the current directory without '..', or an absolute path.",
                            )
                    # 删/移作用于链接本身，其余写目标文件。
                    follow = op.operation in (OperationType.ADD, OperationType.UPDATE)
                    op.file_path = _target(file_ops, op.file_path, follow_symlinks=follow)
                    targets.append(op.file_path)
                    if op.new_path:
                        op.new_path = _target(file_ops, op.new_path, follow_symlinks=False)
                        targets.append(op.new_path)
            else:
                return tool_error(f"Unknown mode: {mode!r}. Use 'replace' or 'patch'.")

            for target in targets:
                if sensitive_err := _sensitive_write_error(target, local):
                    return tool_error(sensitive_err)

            unique_targets = sorted(set(targets))
            # 固定加锁顺序防死锁。
            with ExitStack() as locks:
                for target in unique_targets:
                    locks.enter_context(lock_path(target))
                warnings = [w for t in unique_targets if local and (w := check_stale(task_id, t, whole_file=False))]
                result: PatchResult
                if mode == "replace":
                    result = file_ops.patch_replace(targets[0], old_string or "", new_string or "", replace_all)
                else:
                    result = apply_v4a_operations(operations, file_ops)
                if local and result.success:
                    for target in unique_targets:
                        note_write(task_id, target)

            result_dict = result.to_dict()
            if mode == "replace" and local:
                result_dict["resolved_path"] = targets[0]
            if warnings:
                result_dict["_warning"] = " | ".join(warnings)
            error = result.error or ""
            if "Could not find" in error and "Did you mean one of these sections?" not in error:
                result_dict["_hint"] = (
                    "old_string not found. Use read_file to verify the current content, or search_files to locate the text."
                )
            return json.dumps(result_dict, ensure_ascii=False)
    except Exception as e:
        return tool_error(str(e))


def search_tool(
    pattern: str,
    target: str = "content",
    path: str = ".",
    file_glob: str | None = None,
    limit: int = 50,
    offset: int = 0,
    output_mode: str = "content",
    context: int = 0,
    task_id: str = "default",
) -> str:
    """搜索内容或文件名。"""
    try:
        offset, limit = normalize_search_pagination(offset, limit)
        with _file_ops(task_id) as file_ops:
            if block_error := get_read_block_error(_target(file_ops, path)):
                return tool_error(block_error)
            result = file_ops.search(
                pattern=pattern,
                path=path,
                target=target,
                file_glob=file_glob,
                limit=limit,
                offset=offset,
                output_mode=output_mode,
                context=context,
            )
            for m in result.matches:
                m.content = redact_sensitive_text(m.content)
            result_dict = result.to_dict()
            if result.truncated and not result.hint:
                result_dict["hint"] = (
                    f"Results truncated. Use offset={offset + limit} to see more, "
                    "or narrow with a more specific pattern or file_glob."
                )
            return json.dumps(result_dict, ensure_ascii=False)
    except Exception as e:
        return tool_error(str(e))


LIST_DIRECTORY_SCHEMA = {
    "name": "list_directory",
    "description": "List the contents of a directory. Returns file and folder names, sizes, and modification times.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the directory to list (absolute, relative, or ~/path)",
            },
        },
        "required": ["path"],
    },
}

READ_FILE_SCHEMA = {
    "name": "read_file",
    "description": (
        "Read a text file with line numbers and pagination. Use this instead of cat/head/tail "
        "in terminal. Output format: 'LINE_NUM|CONTENT'. Suggests similar filenames if not "
        "found. Reads exceeding ~100K characters are rejected; use offset and limit to read "
        "specific sections of large files. Cannot read images or other binary files."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file to read (absolute, relative, or ~/path)",
            },
            "offset": {
                "type": "integer",
                "description": "Line number to start reading from (1-indexed, default: 1)",
                "default": 1,
                "minimum": 1,
            },
            "limit": {
                "type": "integer",
                "description": (
                    f"Maximum number of lines to read (default: {DEFAULT_READ_LIMIT}, max: {DEFAULT_MAX_LINES})"
                ),
                "default": DEFAULT_READ_LIMIT,
                "maximum": DEFAULT_MAX_LINES,
            },
        },
        "required": ["path"],
    },
}

WRITE_FILE_SCHEMA = {
    "name": "write_file",
    "description": (
        "Write content to a file, completely replacing existing content. Use this instead of "
        "echo/cat heredoc in terminal. Creates parent directories automatically. OVERWRITES "
        "the entire file — use 'patch' for targeted edits. Auto-runs syntax checks on "
        ".py/.json/.yaml/.toml and other linted languages; only NEW errors introduced by this "
        "write are surfaced (pre-existing errors are filtered out)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Path to the file to write (will be created if it doesn't exist, overwritten if it does)"
                ),
            },
            "content": {"type": "string", "description": "Complete content to write to the file"},
        },
        "required": ["path", "content"],
    },
}

PATCH_SCHEMA = {
    "name": "patch",
    "description": (
        "Targeted find-and-replace edits in files. Use this instead of sed/awk in terminal. "
        "Uses fuzzy matching so minor whitespace/indentation differences won't break it. "
        "Returns a unified diff. Auto-runs syntax checks after editing.\n\n"
        "REPLACE MODE (mode='replace', default): find a unique string and replace it. "
        "REQUIRED PARAMETERS: mode, path, old_string, new_string.\n"
        "PATCH MODE (mode='patch'): apply V4A multi-file patches for bulk changes. "
        "REQUIRED PARAMETERS: mode, patch."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": ["replace", "patch"],
                "description": (
                    "Edit mode. 'replace' (default): requires path + old_string + new_string. "
                    "'patch': requires patch content only."
                ),
                "default": "replace",
            },
            "path": {"type": "string", "description": "REQUIRED when mode='replace'. File path to edit."},
            "old_string": {
                "type": "string",
                "description": (
                    "REQUIRED when mode='replace'. Exact text to find and replace. "
                    "Must be unique in the file unless replace_all=true. Include surrounding "
                    "context lines to ensure uniqueness."
                ),
            },
            "new_string": {
                "type": "string",
                "description": (
                    "REQUIRED when mode='replace'. Replacement text. Pass empty string '' to delete the matched text."
                ),
            },
            "replace_all": {
                "type": "boolean",
                "description": "Replace all occurrences instead of requiring a unique match (default: false)",
                "default": False,
            },
            "patch": {
                "type": "string",
                "description": (
                    "REQUIRED when mode='patch'. V4A format patch content. Format:\n"
                    "*** Begin Patch\n*** Update File: path/to/file\n"
                    "@@ context hint @@\n"
                    " context line\n-removed line\n+added line\n"
                    "*** End Patch"
                ),
            },
        },
        "required": ["mode"],
    },
}

SEARCH_FILES_SCHEMA = {
    "name": "search_files",
    "description": (
        "Search file contents or find files by name. Use this instead of grep/rg/find in terminal. "
        "Hidden files and directories are skipped.\n\n"
        "Content search (target='content'): Regex search inside files. Output modes: full "
        "matches with line numbers, file paths only, or match counts.\n\n"
        "File search (target='files'): Find files by glob pattern (e.g., '*.py', '*config*'); "
        "results sorted by modification time, newest first."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Regex pattern for content search, or glob pattern (e.g., '*.py') for file search",
            },
            "target": {
                "type": "string",
                "enum": ["content", "files"],
                "description": "'content' searches inside file contents, 'files' searches for files by name",
                "default": "content",
            },
            "path": {
                "type": "string",
                "description": "Directory or file to search in (default: current working directory)",
                "default": ".",
            },
            "file_glob": {
                "type": "string",
                "description": "Only search files matching this glob in content search (e.g., '*.py')",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of results to return (default: 50)",
                "default": 50,
            },
            "offset": {
                "type": "integer",
                "description": "Skip first N results for pagination (default: 0)",
                "default": 0,
            },
            "output_mode": {
                "type": "string",
                "enum": ["content", "files_only", "count"],
                "description": (
                    "Output format for content search: 'content' shows matching lines with line "
                    "numbers, 'files_only' lists file paths, 'count' shows match counts per file"
                ),
                "default": "content",
            },
            "context": {
                "type": "integer",
                "description": "Number of context lines before and after each match (content search only)",
                "default": 0,
            },
        },
        "required": ["pattern"],
    },
}


def _handle_list_directory(args: dict[str, Any], **kw: Any) -> str:
    return list_directory_tool(args.get("path", ""), kw.get("task_id", "default"))


def _handle_read_file(args: dict[str, Any], **kw: Any) -> str:
    return read_file_tool(
        args.get("path", ""),
        args.get("offset", 1),
        args.get("limit", DEFAULT_READ_LIMIT),
        kw.get("task_id", "default"),
    )


def _handle_write_file(args: dict[str, Any], **kw: Any) -> str:
    if not isinstance(path := args.get("path"), str) or not path:
        return tool_error("write_file: missing 'path'.")
    if "content" not in args:
        return tool_error("write_file: missing 'content'.")
    if not isinstance(content := args["content"], str):
        return tool_error(f"write_file: 'content' must be string, got {type(content).__name__}.")
    return write_file_tool(path, content, kw.get("task_id", "default"))


def _handle_patch(args: dict[str, Any], **kw: Any) -> str:
    return patch_tool(
        args.get("mode", "replace"),
        args.get("path"),
        args.get("old_string"),
        args.get("new_string"),
        bool(args.get("replace_all", False)),
        args.get("patch"),
        kw.get("task_id", "default"),
    )


def _handle_search_files(args: dict[str, Any], **kw: Any) -> str:
    return search_tool(
        args.get("pattern", ""),
        args.get("target", "content"),
        args.get("path", "."),
        args.get("file_glob"),
        args.get("limit", 50),
        args.get("offset", 0),
        args.get("output_mode", "content"),
        args.get("context", 0),
        kw.get("task_id", "default"),
    )


registry.register_tool("list_directory", schema=LIST_DIRECTORY_SCHEMA)(_handle_list_directory)
registry.register_tool("read_file", schema=READ_FILE_SCHEMA)(_handle_read_file)
registry.register_tool("write_file", schema=WRITE_FILE_SCHEMA)(_handle_write_file)
registry.register_tool("patch", schema=PATCH_SCHEMA)(_handle_patch)
registry.register_tool("search_files", schema=SEARCH_FILES_SCHEMA)(_handle_search_files)
