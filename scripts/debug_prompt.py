#!/usr/bin/env python3
import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

if sys.platform == "win32" and sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if sys.platform == "win32" and sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

DEFAULT_MOCK_PERSONA: dict[str, str] = {
    "name": "露娜 (Luna)",
    "personality": "活泼、好奇心强、贴心且偶尔有点小傲娇，说话真诚直接",
    "speaking_style": "自然亲切，常用轻快的语气，习惯用简短生动的句子",
    "appearance": "银白色长发配猫耳，深蓝色眼眸，身穿浅色宽松连帽衫",
    "relationship": "陪伴在侧的知心好友与工作助手",
    "biological_type": "猫娘",
    "gender": "女性",
}

DEFAULT_MOCK_USER_PROFILE: dict[str, str] = {
    "preferred_name": "阿明",
    "gender": "男",
    "age_bucket": "26-35岁",
    "hobbies": "写代码、打游戏、看动漫、听音乐",
    "freeform": "平时经常写代码，希望你在旁边陪伴聊天并在需要时协助分析问题",
}

DEFAULT_MOCK_SKILLS: list[str] = ["browser", "coding", "search"]

# 双语 mock 用户资料块标题（仅 debug 脚本内使用）。
_MOCK_USER_PROFILE_LABELS_TEXTS: dict[str, str] = {
    "zh": "# 用户资料",
    "en": "# User profile",
}


def _build_mock_user_profile_extras(profile: dict[str, str], *, language: str = "zh") -> str:
    from components import resolve_prompt_text

    lines = [resolve_prompt_text(_MOCK_USER_PROFILE_LABELS_TEXTS, language)]
    for key, val in profile.items():
        if val:
            display = key.replace("_", " ").capitalize()
            lines.append(f"- **{display}**: {val}")
    return "\n".join(lines)


async def _load_from_db(user_id: int, *, language: str = "zh") -> dict[str, Any]:
    from components import SESSION_LOCAL
    from modules.companion import Persona
    from services.companion import (
        build_outfit_extras,
        build_system_prompt_extras,
        build_user_profile_extras,
        format_auto_inject_block,
        format_inferred_profile_block,
        format_proactive_memory_block,
    )
    from services.tools import REGISTRY
    from sqlalchemy import select

    async with SESSION_LOCAL() as db:
        persona = (await db.execute(select(Persona).where(Persona.user_id == user_id))).scalar_one_or_none()
        persona_extras = build_system_prompt_extras(persona, language=language) if persona else ""

        user_profile_extras = (
            await build_user_profile_extras(db, user_id, language=language) if persona and persona.is_complete else ""
        )
        outfit_extras = (
            await build_outfit_extras(db, user_id, language=language) if persona and persona.is_complete else ""
        )
        auto_inject_extras = await format_auto_inject_block(db, user_id, language=language)
        inferred_profile_extras = await format_inferred_profile_block(db, user_id, language=language)
        proactive_memory_extras = format_proactive_memory_block([], language=language)

        tools = REGISTRY.get_all_schemas(user_id=user_id, user_settings={})

        return {
            "persona_extras": persona_extras,
            "user_profile_extras": user_profile_extras,
            "outfit_extras": outfit_extras,
            "auto_inject_extras": auto_inject_extras,
            "inferred_profile_extras": inferred_profile_extras,
            "proactive_memory_extras": proactive_memory_extras,
            "tools": tools,
        }


def assemble_debug_prompt(
    *,
    message_text: str,
    persona_dict: dict[str, str],
    user_profile_dict: dict[str, str],
    skills: list[str],
    model: str,
    language: str,
    platform: str,
    enable_tools: bool,
    outfit_text: str = "",
    auto_inject_text: str = "",
    db_data: dict[str, Any] | None = None,
    preset_id: str = "companion",
    user_local_tz: str | None = None,
    message_sent_at: Any | None = None,
) -> dict[str, Any]:
    from datetime import datetime
    from types import SimpleNamespace

    from components import ensure_utc, utc_now
    from modules.auth import ChatRequestClientContext
    from modules.system import AgentPromptConfig
    from services.chat.prompt_presets import BUILTIN_PRESETS, resolve_preset
    from services.chat.system_prompt import build_system_prompt
    from services.chat.turn_inputs import _history_to_responses_context
    from services.companion import render_extras
    from services.llm import approx_responses_tokens
    from services.tools import REGISTRY, schema_name

    if db_data is not None:
        persona_extras = db_data["persona_extras"]
        user_profile_extras = db_data["user_profile_extras"]
        outfit_extras = db_data["outfit_extras"]
        auto_inject_extras = db_data["auto_inject_extras"]
        inferred_profile_extras = db_data["inferred_profile_extras"]
        proactive_memory_extras = db_data["proactive_memory_extras"]
        tools = db_data["tools"] if enable_tools else []
    else:
        persona_extras = render_extras(persona_dict, language=language)
        user_profile_extras = _build_mock_user_profile_extras(user_profile_dict, language=language)
        outfit_extras = outfit_text
        auto_inject_extras = auto_inject_text
        inferred_profile_extras = ""
        proactive_memory_extras = ""
        tools = REGISTRY.get_all_schemas(user_id=1, user_settings={}) if enable_tools else []

    valid_tool_names = [schema_name(s) for s in tools]

    client_ctx = ChatRequestClientContext(
        skills=skills,
        environment_hints=f"OS: {sys.platform}; Workspace: {REPO_ROOT.as_posix()}",
        platform_hints=None,
    )

    agent_config = AgentPromptConfig(
        valid_tool_names=valid_tool_names,
        model=model,
        tools=tools,
        client_context=client_ctx,
        identity_prompt=None,
        persona_extras=persona_extras,
        user_profile_extras=user_profile_extras,
        outfit_extras=outfit_extras,
        auto_inject_extras=auto_inject_extras,
        inferred_profile_extras=inferred_profile_extras,
        proactive_memory_extras=proactive_memory_extras,
        language=language,
        platform=platform,
        user_local_tz=user_local_tz,
    )

    instructions = build_system_prompt(agent_config, preset=resolve_preset(preset_id))

    # 走生产装配：构造 ORM-like mock Message，让陪伴预设带上日期分界与时刻提示。
    if message_sent_at is not None:
        parsed = datetime.fromisoformat(message_sent_at) if isinstance(message_sent_at, str) else message_sent_at
        sent_at = ensure_utc(parsed)
    else:
        sent_at = ensure_utc(utc_now())
    mock_msg = SimpleNamespace(
        subtype=None,
        role="user",
        content=message_text,
        content_type="text",
        created_at=sent_at,
        tool_calls=None,
        tool_call_id=None,
    )
    resolved = resolve_preset(preset_id)
    input_items = _history_to_responses_context(
        [mock_msg],
        instructions,
        user_local_tz=user_local_tz,
        lang=language,
        inject_time_perception=resolved.id == "companion",
    )["input"]

    estimated_tokens = approx_responses_tokens(instructions, input_items)

    active_tools = [s for s in tools if schema_name(s) == "search_tools"] if enable_tools else []

    return {
        "model": model,
        "instructions": instructions,
        "input": input_items,
        "tools": active_tools,
        "metadata": {
            "estimated_tokens": estimated_tokens,
            "language": language,
            "platform": platform,
            "active_tool_names": [schema_name(s) for s in active_tools],
            "available_tool_names": valid_tool_names,
            "persona_name": persona_dict.get("name", ""),
            "preset_id": preset_id,
            "preset_name": BUILTIN_PRESETS[resolve_preset(preset_id).id].name,
            "user_local_tz": user_local_tz or "",
            "message_sent_at": sent_at.isoformat() if sent_at else "",
        },
    }


def format_human_readable(result: dict[str, Any]) -> str:
    from services.tools import schema_name

    meta = result["metadata"]
    lines: list[str] = []
    separator = "=" * 80
    sub_separator = "-" * 80

    lines.append(separator)
    lines.append("SpiritAgent 提示词调试输出 (Prompt Debug Inspection)")
    lines.append(separator)
    lines.append(f"• 语言环境 (Language): {meta['language']}")
    lines.append(f"• 运行平台 (Platform): {meta['platform']}")
    lines.append(f"• 系统预设 (Preset): {meta['preset_id']} ({meta['preset_name']})")
    lines.append(f"• Token 估算 (Estimated Tokens): ~{meta['estimated_tokens']}")
    lines.append(f"• 活跃工具数 (Active Tools): {len(meta['active_tool_names'])} 个")
    lines.append("")

    lines.append(separator)
    lines.append("【 1. 系统提示词 (System Prompt / Instructions) 】")
    lines.append(separator)
    lines.append(result["instructions"])
    lines.append("")

    lines.append(separator)
    lines.append("【 2. 用户输入消息 (Input Messages) 】")
    lines.append(separator)
    lines.append(json.dumps(result["input"], ensure_ascii=False, indent=2))
    lines.append("")

    lines.append(separator)
    lines.append("【 3. 注册工具列表 (Active Tools Schema Summary) 】")
    lines.append(separator)
    for tool in result["tools"]:
        name = schema_name(tool)
        desc = (tool.get("description") or tool.get("function", {}).get("description", "")).strip()
        lines.append(f"  • {name}: {desc[:80]}...")
    lines.append("")

    lines.append(sub_separator)
    lines.append("完整请求负载已就绪。")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="调试 SpiritAgent 完成 Onboarding 及接收用户消息时的完整提示词与请求负载",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "-m",
        "--message",
        default="你好！我刚完成了初始设定，以后请多关照啦～",
        help="模拟用户发出的消息内容",
    )
    parser.add_argument("--model", default="default", help="大模型槽位名称")
    parser.add_argument(
        "--preset",
        default="companion",
        choices=["companion", "developer", "product_manager", "copywriter", "language_teacher"],
        help="系统提示词预设 id；默认 companion(完整伴侣语气)，其余工作面预设抑制伴侣 persona 与着装联动",
    )
    parser.add_argument("--language", choices=["zh", "en"], default="zh", help="回复语言")
    parser.add_argument(
        "--platform",
        choices=["desktop", "wechat"],
        default="desktop",
        help="交互平台标识 (desktop, wechat)",
    )
    parser.add_argument("--without-tools", action="store_true", help="禁用工具注入")

    # 时间感知（与生产路径一致）
    parser.add_argument(
        "--user-tz",
        default=None,
        help="用户本地 IANA 时区（如 Asia/Shanghai）；让 volatile header 与陪伴时间提示走本地时区",
    )
    parser.add_argument(
        "--message-sent-at",
        default=None,
        help="模拟用户消息的发送时刻（ISO 8601，如 2026-08-29T02:30:00+08:00）；影响陪伴时间提示",
    )

    # Onboarding 角色相关参数
    parser.add_argument("--persona-name", default=DEFAULT_MOCK_PERSONA["name"], help="角色姓名")
    parser.add_argument("--personality", default=DEFAULT_MOCK_PERSONA["personality"], help="角色性格")
    parser.add_argument(
        "--speaking-style",
        default=DEFAULT_MOCK_PERSONA["speaking_style"],
        help="说话风格",
    )
    parser.add_argument("--appearance", default=DEFAULT_MOCK_PERSONA["appearance"], help="外貌描述")
    parser.add_argument(
        "--relationship",
        default=DEFAULT_MOCK_PERSONA["relationship"],
        help="与用户关系",
    )
    parser.add_argument(
        "--species",
        default=DEFAULT_MOCK_PERSONA["biological_type"],
        help="物种/生物类型",
    )
    parser.add_argument("--gender", default=DEFAULT_MOCK_PERSONA["gender"], help="角色性别")

    # Onboarding 用户资料相关参数
    parser.add_argument(
        "--user-name",
        default=DEFAULT_MOCK_USER_PROFILE["preferred_name"],
        help="用户称呼",
    )
    parser.add_argument("--user-gender", default=DEFAULT_MOCK_USER_PROFILE["gender"], help="用户性别")
    parser.add_argument(
        "--user-age",
        default=DEFAULT_MOCK_USER_PROFILE["age_bucket"],
        help="用户年龄段",
    )
    parser.add_argument(
        "--user-hobbies",
        default=DEFAULT_MOCK_USER_PROFILE["hobbies"],
        help="用户爱好",
    )
    parser.add_argument(
        "--user-freeform",
        default=DEFAULT_MOCK_USER_PROFILE["freeform"],
        help="用户补充背景",
    )

    # 技能
    parser.add_argument(
        "--skills",
        default=",".join(DEFAULT_MOCK_SKILLS),
        help="启用的本地技能列表 (逗号分隔)",
    )

    # 数据库模式
    parser.add_argument("--db", action="store_true", help="连接 PostgreSQL 数据库读取真实用户数据")
    parser.add_argument("--user-id", type=int, default=1, help="数据库查询对应的 user_id")

    # 输出模式
    parser.add_argument(
        "--raw",
        "--json",
        dest="raw_json",
        action="store_true",
        help="以原始 JSON 格式输出请求体",
    )
    parser.add_argument(
        "--system-only",
        action="store_true",
        help="仅输出组装后的系统提示词 (Instructions)",
    )
    parser.add_argument("--save", type=Path, default=None, help="将输出保存到指定文件")

    args = parser.parse_args()

    db_data = None
    if args.db:
        try:
            db_data = asyncio.run(_load_from_db(args.user_id, language=args.language))
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"Error loading from database: {exc}", file=sys.stderr)
            return 1

    persona_dict = {
        "name": args.persona_name,
        "personality": args.personality,
        "speaking_style": args.speaking_style,
        "appearance": args.appearance,
        "relationship": args.relationship,
        "biological_type": args.species,
        "gender": args.gender,
    }

    user_profile_dict = {
        "preferred_name": args.user_name,
        "gender": args.user_gender,
        "age_bucket": args.user_age,
        "hobbies": args.user_hobbies,
        "freeform": args.user_freeform,
    }

    skills = [s.strip() for s in args.skills.split(",") if s.strip()]

    result = assemble_debug_prompt(
        message_text=args.message,
        persona_dict=persona_dict,
        user_profile_dict=user_profile_dict,
        skills=skills,
        model=args.model,
        language=args.language,
        platform=args.platform,
        enable_tools=not args.without_tools,
        db_data=db_data,
        preset_id=args.preset,
        user_local_tz=args.user_tz,
        message_sent_at=args.message_sent_at,
    )

    if args.raw_json:
        output_str = json.dumps(
            {
                "model": result["model"],
                "instructions": result["instructions"],
                "input": result["input"],
                "tools": result["tools"],
                "metadata": result["metadata"],
            },
            ensure_ascii=False,
            indent=2,
        )
    elif args.system_only:
        output_str = result["instructions"]
    else:
        output_str = format_human_readable(result)

    print(output_str)

    if args.save:
        args.save.parent.mkdir(parents=True, exist_ok=True)
        args.save.write_text(output_str, encoding="utf-8")
        print(f"\n[Saved output to {args.save}]", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
