"""视频演绎脚本：LLM 按角色性格撰写每个动作的表现提示词，模板固定动作集合与顺序。

脚本只是生成视频提示词的中间产物：seconds 为节奏提示，不用于精确切分（动作分割
依赖空白间隔与运动能量兜底，见 segmentation.py）。校验与归一由代码完成，模型只负责语义。
"""

import json
import math

from components import get_logger, parse_llm_json
from modules.companion import REQUIRED_VIDEO_ACTIONS
from prompts.generation import (
    VIDEO_ACTION_SCRIPT_INSTRUCTIONS,
    VIDEO_ACTION_SEMANTICS,
    VIDEO_PROMPT_SKELETON,
    VIDEO_TIMELINE_ITEM_TEMPLATE,
)
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from services.infrastructure.llm import chat

logger = get_logger(__name__)

MIN_ACTION_SECONDS = 1.5
MAX_ACTION_SECONDS = 3.0
DEFAULT_ACTION_SECONDS = 2.0
MOTION_PROMPT_MAX_CHARS = 160
# 动作之外给空白间隔留出的节奏余量（3 个间隔 × 0.4s）
_BLANK_RESERVE_SECONDS = 1.2


class VideoScriptError(RuntimeError):
    """演绎脚本生成或校验失败；str 为公开文案。"""


class ActionScriptEntry(BaseModel):
    """单个动作的表现提示：动作键 + 中文动作描述 + 节奏提示（秒）。"""

    action: str
    motion_prompt: str = Field(min_length=1)
    seconds: float = Field(gt=0)


class ActionScript(BaseModel):
    """整包演绎脚本：按 REQUIRED_VIDEO_ACTIONS 顺序排列。"""

    actions: list[ActionScriptEntry]


def _normalize_script(raw: object, *, duration_budget: float) -> ActionScript:
    """解析并归一 LLM 输出：动作集合与顺序以固定模板为准，缺失即失败；
    seconds 只作节奏提示，钳制后按预算等比收缩，不因数值问题丢弃已生成的语义。"""
    if not isinstance(raw, dict) or not isinstance(raw.get("actions"), list):
        raise VideoScriptError("动作脚本结构不符合要求")

    by_action: dict[str, dict] = {}
    for entry in raw["actions"]:
        if isinstance(entry, dict) and isinstance(entry.get("action"), str):
            by_action[entry["action"]] = entry

    missing = [action for action in REQUIRED_VIDEO_ACTIONS if action not in by_action]
    if missing:
        raise VideoScriptError("动作脚本缺少动作：" + "、".join(missing))

    entries: list[ActionScriptEntry] = []
    for action in REQUIRED_VIDEO_ACTIONS:
        entry = by_action[action]
        motion = str(entry.get("motion_prompt") or "").strip()
        if not motion:
            raise VideoScriptError(f"动作 {action} 缺少表现描述")
        try:
            seconds = float(entry.get("seconds"))
        except (TypeError, ValueError):
            seconds = DEFAULT_ACTION_SECONDS
        if not math.isfinite(seconds):
            seconds = DEFAULT_ACTION_SECONDS
        seconds = min(MAX_ACTION_SECONDS, max(MIN_ACTION_SECONDS, seconds))
        entries.append(
            ActionScriptEntry(
                action=action,
                motion_prompt=motion[:MOTION_PROMPT_MAX_CHARS],
                seconds=seconds,
            ),
        )

    _fit_seconds(entries, duration_budget=duration_budget)
    return ActionScript(actions=entries)


def _fit_seconds(entries: list[ActionScriptEntry], *, duration_budget: float) -> None:
    """把节奏提示压进时长预算：单值钳制后总和仍超预算则等比收缩。"""
    for entry in entries:
        entry.seconds = min(MAX_ACTION_SECONDS, max(MIN_ACTION_SECONDS, entry.seconds))
    max_total = max(float(len(entries)) * MIN_ACTION_SECONDS, duration_budget - _BLANK_RESERVE_SECONDS)
    total = sum(entry.seconds for entry in entries)
    if total > max_total and total > 0:
        scale = max_total / total
        for entry in entries:
            entry.seconds = round(entry.seconds * scale, 1)


def build_script_payload(
    *,
    persona_definition: dict[str, str],
    personality_tags: list[str],
    outfit_description: str,
    duration_budget: float,
) -> dict:
    """装配脚本生成输入；purpose 为固定模板给出的动作用途，模型不得更改动作集合。"""
    return {
        "persona": {
            key: persona_definition.get(key) or ""
            for key in ("name", "biological_type", "gender", "appearance", "personality", "speaking_style")
        },
        "personality_tags": personality_tags,
        "outfit_description": outfit_description,
        "duration_budget": duration_budget,
        "actions": [{"action": action, "purpose": purpose} for action, purpose in VIDEO_ACTION_SEMANTICS.items()],
    }


async def compose_action_script(
    db: AsyncSession | None,
    user_id: int,
    *,
    persona_definition: dict[str, str],
    personality_tags: list[str],
    outfit_description: str,
    duration_budget: float,
) -> ActionScript:
    """调用 LLM 生成演绎脚本；结构不符时重试一次，仍失败则抛公开文案错误。"""
    payload = build_script_payload(
        persona_definition=persona_definition,
        personality_tags=personality_tags,
        outfit_description=outfit_description,
        duration_budget=duration_budget,
    )
    last_error = "模型未返回脚本"
    for _attempt in range(2):
        raw = await chat(db, user_id, VIDEO_ACTION_SCRIPT_INSTRUCTIONS, json.dumps(payload, ensure_ascii=False))
        try:
            return _normalize_script(parse_llm_json(raw), duration_budget=duration_budget)
        except VideoScriptError as exc:
            last_error = str(exc)
            logger.warning("action script attempt rejected", extra={"user_id": user_id, "reason": last_error})
    raise VideoScriptError(f"动作脚本生成失败（{last_error}），请重试")


def build_video_prompt(script: ActionScript) -> str:
    """由脚本组装单条视频提示词：骨架条款固定，脚本只填充动作时间线。"""
    timeline = "\n".join(
        VIDEO_TIMELINE_ITEM_TEMPLATE.format(
            index=index + 1,
            action=entry.action,
            seconds=entry.seconds,
            motion=entry.motion_prompt,
        )
        for index, entry in enumerate(script.actions)
    )
    return VIDEO_PROMPT_SKELETON.format(timeline=timeline)
