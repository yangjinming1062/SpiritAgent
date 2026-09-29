from components import safe_json_loads

_FILE_MUTATING_TOOL_NAMES = frozenset({"write_file", "patch"})


def file_mutation_result_landed(tool_name: str, result: str) -> bool:
    """文件写入结果能证明写入确实落到磁盘时返回 True。"""
    if tool_name not in _FILE_MUTATING_TOOL_NAMES:
        return False
    data = safe_json_loads(result.strip())
    if not isinstance(data, dict) or data.get("error"):
        return False
    return ("bytes_written" in data) if tool_name == "write_file" else data.get("success") is True
