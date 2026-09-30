"""Slash 命令注册表与匹配；设计取舍见 chat/README.md。"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Literal

from components import get_logger

logger = get_logger(__name__)

SlashCommandStatus = Literal["ok", "error"]


@dataclass(slots=True)
class SlashCommandContext:
    """命令 handler 的执行上下文。``runtime`` / ``dispatcher`` 用 ``Any`` 避免导入适配层（实为 RuntimeSession / JsonRpcDispatcher）。"""

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
    # 结果改变历史（如 /清理 /压缩）时客户端用 payload.messages 替换本地消息列表；与 hydrate 同源。
    hydrate: bool = False


SlashCommandHandler = Callable[[SlashCommandContext], Awaitable[SlashCommandResult]]


@dataclass(slots=True)
class SlashCommand:
    name: str  # 主名（无 /，小写）
    aliases: list[str] = field(default_factory=list)
    description: str = ""
    requires_confirmation: bool = False
    handler: SlashCommandHandler | None = None


# name 为主键（小写、去 / 前缀）；aliases 镜像到同一 SlashCommand 实例。
SLASH_COMMANDS: dict[str, SlashCommand] = {}


def register(
    *,
    name: str,
    aliases: tuple[str, ...] | list[str] = (),
    description: str = "",
    requires_confirmation: bool = False,
) -> Callable[[SlashCommandHandler], SlashCommandHandler]:
    """装饰器：注册协程为 SlashCommand。主名与别名共享同一实例；重复注册同 key 时后者覆盖并 warning。"""

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


def suggest_commands(name: str, *, limit: int = 3, cutoff: float = 0.5) -> list[str]:
    """未识别命令的相似推荐（主名 + aliases，CJK 别名可命中）；SequenceMatcher 降序去重，``cutoff=0.5`` 过滤弱匹配。"""
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
