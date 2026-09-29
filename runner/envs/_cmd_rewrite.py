import re

from utils import cfg_get, load_config

_ENV_ASSIGN_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _looks_like_env_assignment(token: str) -> bool:
    if "=" not in token or token.startswith("="):
        return False
    name, _value = token.split("=", 1)
    return bool(_ENV_ASSIGN_NAME_RE.match(name))


def _read_shell_token(command: str, start: int) -> tuple[str, int]:
    i = start
    n = len(command)
    while i < n:
        ch = command[i]
        if ch.isspace() or ch in ";|&()":
            break
        if ch == "'":
            i += 1
            while i < n and command[i] != "'":
                i += 1
            if i < n:
                i += 1
            continue
        if ch == '"':
            i += 1
            while i < n:
                inner = command[i]
                if inner == "\\" and i + 1 < n:
                    i += 2
                    continue
                if inner == '"':
                    i += 1
                    break
                i += 1
            continue
        if ch == "\\" and i + 1 < n:
            i += 2
            continue
        i += 1
    return command[start:i], i


def _rewrite_real_sudo_invocations(command: str) -> tuple[str, bool]:
    out: list[str] = []
    i = 0
    n = len(command)
    command_start = True
    found = False
    while i < n:
        ch = command[i]
        if ch.isspace():
            out.append(ch)
            if ch == "\n":
                command_start = True
            i += 1
            continue
        if ch == "#" and command_start:
            comment_end = command.find("\n", i)
            if comment_end == -1:
                out.append(command[i:])
                break
            out.append(command[i:comment_end])
            i = comment_end
            continue
        if command.startswith("&&", i) or command.startswith("||", i) or command.startswith(";;", i):
            out.append(command[i : i + 2])
            i += 2
            command_start = True
            continue
        if ch in ";|&(":
            out.append(ch)
            i += 1
            command_start = True
            continue
        if ch == ")":
            out.append(ch)
            i += 1
            command_start = False
            continue
        token, next_i = _read_shell_token(command, i)
        if command_start and token == "sudo":
            out.append("sudo -S -p ''")
            found = True
        else:
            out.append(token)
        command_start = bool(command_start and _looks_like_env_assignment(token))
        i = next_i
    return "".join(out), found


def _rewrite_compound_background(command: str) -> str:
    n = len(command)
    i = 0
    paren_depth = 0
    brace_depth = 0
    last_chain_op_end = -1
    rewrites: list[tuple[int, int]] = []
    while i < n:
        ch = command[i]
        if ch == "\n" and paren_depth == 0 and brace_depth == 0:
            last_chain_op_end = -1
            i += 1
            continue
        if ch.isspace():
            i += 1
            continue
        if ch == "#":
            nl = command.find("\n", i)
            if nl == -1:
                break
            i = nl
            continue
        if ch == "\\" and i + 1 < n:
            i += 2
            continue
        if ch in {"'", '"'}:
            _, next_i = _read_shell_token(command, i)
            i = max(next_i, i + 1)
            continue
        if ch == "(":
            paren_depth += 1
            i += 1
            continue
        if ch == ")":
            paren_depth = max(0, paren_depth - 1)
            i += 1
            continue
        if ch == "{" and i + 1 < n and (command[i + 1].isspace() or command[i + 1] == "\n"):
            brace_depth += 1
            i += 1
            continue
        if ch == "}" and brace_depth > 0:
            brace_depth -= 1
            last_chain_op_end = -1
            i += 1
            continue
        if paren_depth > 0 or brace_depth > 0:
            i += 1
            continue
        if command.startswith("&&", i) or command.startswith("||", i):
            last_chain_op_end = i + 2
            i += 2
            continue
        if ch == ";":
            last_chain_op_end = -1
            i += 1
            continue
        if ch == "|":
            last_chain_op_end = -1
            i += 1
            continue
        if ch == "&":
            if i + 1 < n and command[i + 1] == ">":
                i += 2
                continue
            j = i - 1
            while j >= 0 and command[j].isspace():
                j -= 1
            if j >= 0 and command[j] in "<>":
                i += 1
                continue
            if last_chain_op_end >= 0:
                rewrites.append((last_chain_op_end, i))
            last_chain_op_end = -1
            i += 1
            continue
        _, next_i = _read_shell_token(command, i)
        i = max(next_i, i + 1)
    if not rewrites:
        return command
    result = command
    for chain_end, amp_pos in reversed(rewrites):
        insert_pos = chain_end
        while insert_pos < amp_pos and result[insert_pos].isspace():
            insert_pos += 1
        prefix = result[:insert_pos]
        middle = result[insert_pos:amp_pos]
        suffix = result[amp_pos + 1 :]
        result = prefix + "{ " + middle + "& }" + suffix
    return result


def _transform_sudo_command(command: str) -> tuple[str, str | None]:
    """配置了 terminal.sudo_password 且命令含真实 sudo 调用时改写为 `sudo -S -p ''`，返回需经 stdin 提供的密码。"""
    sudo_password = cfg_get(load_config(), "terminal", "sudo_password", default="")
    if not sudo_password:
        return command, None
    transformed, has_real_sudo = _rewrite_real_sudo_invocations(command)
    return (transformed, f"{sudo_password}\n") if has_real_sudo else (command, None)
