"""Slash 命令注册表与契约。

设计要点：
- 与 BUILTIN_PRESETS 同源（静态 dict），但不混入 preset 体系（命令是回合外副作用，与 system prompt 模板正交）。
- 命令不走 LLM tool_call 路径（避免 LLM 越权触发 / 与 persona 渲染冲突）。
- 客户端本地也有同名元数据镜像（client/renderer/shared/lib/slash-commands.ts），仅用于自动补全与
  confirm 弹窗等 UI 优化；服务端 ``command.dispatch`` 仍是唯一权威。
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Literal

from components import get_logger

logger = get_logger(__name__)


SlashCommandStatus = Literal["ok", "error"]


@dataclass(slots=True)
class SlashCommandContext:
    """命令 handler 收到的执行上下文。

    ``runtime`` 与 ``dispatcher`` 字段类型用 ``Any`` 避免应用层导入适配层；实际是
    ``adapters/desktop/runtime.RuntimeSession`` 与 ``infrastructure/desktop/jsonrpc.JsonRpcDispatcher``。
    """

    session_id: str
    user_id: int
    runtime: Any
    dispatcher: Any
    args: list[str] = field(default_factory=list)
    raw: str = ""
    confirmed: bool = False


@dataclass(slots=True)
class SlashCommandResult:
    """命令执行结果（同时作为 RPC response 与 ``command.result`` 事件 payload 的一部分）。"""

    status: SlashCommandStatus
    message: str
    payload: dict | None = None
    # 当结果改变历史（如 /清理 /压缩），客户端用 payload.messages 替换本地消息列表；与 hydrate 同源。
    hydrate: bool = False


SlashCommandHandler = Callable[[SlashCommandContext], Awaitable[SlashCommandResult]]


@dataclass(slots=True)
class SlashCommand:
    name: str  # 主名（无 /，小写）
    aliases: list[str] = field(default_factory=list)
    description: str = ""
    requires_confirmation: bool = False
    handler: SlashCommandHandler | None = None


# ``name``（小写，剥离前缀 /）作为主键；aliases 镜像到同一 SlashCommand。
SLASH_COMMANDS: dict[str, SlashCommand] = {}


def register(
    *,
    name: str,
    aliases: tuple[str, ...] | list[str] = (),
    description: str = "",
    requires_confirmation: bool = False,
) -> Callable[[SlashCommandHandler], SlashCommandHandler]:
    """装饰器：把协程包装成 SlashCommand 并塞进 ``SLASH_COMMANDS``。

    主名与所有别名共享同一个 SlashCommand 实例（别名只是 key 镜像，调用 resolve() 时无差别）。
    重复注册同 key 时后者覆盖前者并 warning（与 builtin tool 注册语义保持一致）。
    """

    def deco(fn: SlashCommandHandler) -> SlashCommandHandler:
        cmd = SlashCommand(
            name=name,
            aliases=list(aliases),
            description=description,
            requires_confirmation=requires_confirmation,
            handler=fn,
        )
        existing = SLASH_COMMANDS.get(name)
        if existing is not None:
            logger.warning("slash command %r re-registered; overwriting", name)
        SLASH_COMMANDS[name] = cmd
        for alias in cmd.aliases:
            if alias in SLASH_COMMANDS and SLASH_COMMANDS[alias] is not cmd:
                logger.warning(
                    "slash command alias %r already bound to %r; rebinding to %r",
                    alias,
                    SLASH_COMMANDS[alias].name,
                    name,
                )
            SLASH_COMMANDS[alias] = cmd
        return fn

    return deco


def resolve(name: str) -> SlashCommand | None:
    """按名（已剥离前缀 /，小写）查 SlashCommand；未识别返回 None。"""
    return SLASH_COMMANDS.get(name)


def list_commands_for_user() -> list[dict]:
    """供 ``/帮助`` / REST 镜像列出可用命令；去重（aliases 不重复展示）。"""
    seen: set[int] = set()
    out: list[dict] = []
    for cmd in SLASH_COMMANDS.values():
        if id(cmd) in seen:
            continue
        seen.add(id(cmd))
        out.append(
            {
                "name": cmd.name,
                "aliases": list(cmd.aliases),
                "description": cmd.description,
                "requires_confirmation": cmd.requires_confirmation,
            },
        )
    out.sort(key=lambda x: x["name"])
    return out


# --- 模糊建议（给未识别命令回执用） ---


def suggest_commands(name: str, *, limit: int = 3, cutoff: float = 0.5) -> list[str]:
    """未识别命令的回执：返回与 ``name`` 最相近的命令主名列表（不含 aliases）。

    同时扫描主名与所有 aliases（CJK 别名也能命中）；用 stdlib ``difflib.SequenceMatcher``
    计算相似度，按 ratio 降序去重返回。``cutoff=0.5`` 过滤掉一半以上的差异。
    """
    name_l = name.lower()
    scored: dict[str, float] = {}

    for cmd in SLASH_COMMANDS.values():
        for key in (cmd.name, *cmd.aliases):
            ratio = SequenceMatcher(None, name_l, key.lower()).ratio()

            if ratio >= cutoff:
                best = scored.get(cmd.name)

                if best is None or ratio > best:
                    scored[cmd.name] = ratio

    return [name for name, _ in sorted(scored.items(), key=lambda kv: kv[1], reverse=True)[:limit]]
