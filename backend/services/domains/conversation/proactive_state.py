import time
from dataclasses import dataclass
from enum import StrEnum

from components import safe_json_loads
from modules.companion import Persona
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


class ProactiveState(StrEnum):
    """进程内的主动外联状态机（per-user, 模块级字典持有）。
    IDLE / OUTREACHED / FOLLOWUP_SENT 各自承担不同的事件触发语义：

      - IDLE：未在主动外联周期内；带正数跟进间隔的 send_message_tool 转入 OUTREACHED。
      - OUTREACHED：LLM 通过 send_message_tool 发出了主动消息，等候用户响应或第一轮跟进 turn。
      - FOLLOWUP_SENT：cron 已触发过跟进 turn；LLM 仍在节奏内（可在 turn 中再次主动发言）。
        LLM 在跟进 turn 内再次 send_message_tool 时保持该状态并刷新 timestamp，等待下一轮 cron。

    LLM 给出 followup_timeout_seconds=0/None 表示「这一轮主动节奏到此为止」，状态回到 IDLE；
    用户发消息或档位切入静止同样重置回 IDLE。状态机不持久化，进程重启后回到 IDLE。
    """

    IDLE = "idle"
    OUTREACHED = "outreached"
    FOLLOWUP_SENT = "followup_sent"


@dataclass
class UserProactiveRecord:
    state: ProactiveState = ProactiveState.IDLE
    last_outreach_ts: float = 0.0
    last_proactive_text: str = ""
    # 0 表示「无下一次跟进」：LLM 主动给正数表示「我期望这么多秒内用户响应或我再次主动」，
    # 给 0 / None 表示「这一轮主动节奏到此为止」，状态机据此回到 IDLE。
    followup_timeout_seconds: float = 0.0
    # 用户最近一次与伙伴互动（发消息 / 戳摸）的单调时间戳；0 = 进程启动以来尚未互动。
    # 常规档的被冷落问候据此计算「用户多久没理伙伴」。
    last_user_contact_ts: float = 0.0


_USER_PROACTIVE_STATE: dict[int, UserProactiveRecord] = {}


def _get_or_create_rec(user_id: int) -> UserProactiveRecord:
    return _USER_PROACTIVE_STATE.setdefault(user_id, UserProactiveRecord())


async def get_personality_tags(db: AsyncSession, user_id: int) -> list[str]:
    """读 Persona 表的 personality_tags_json；不存在或解析失败返回空列表。"""
    raw = (await db.execute(select(Persona.personality_tags_json).where(Persona.user_id == user_id))).scalar()
    if not raw:
        return []
    parsed = safe_json_loads(raw, default=[])
    return [t for t in parsed if isinstance(t, str) and t] if isinstance(parsed, list) else []


def record_user_outreach(
    user_id: int,
    text: str,
    followup_timeout_seconds: float | None = None,
) -> None:
    """记录 LLM 主动外联事件，并把状态机推入正确的下一态。

    行为契约：
      - IDLE + 正数 timeout：进入 OUTREACHED，等候用户响应或第一轮跟进。
      - IDLE + timeout=None 或 ≤0：记录本次发言但不进入主动节奏（保持 IDLE）。
      - 非 IDLE + timeout=None 或 ≤0：LLM 显式结束本轮主动节奏 → 回到 IDLE。
      - 非 IDLE + 正数 timeout：进入或保持 FOLLOWUP_SENT，等候下一轮 cron 触发
        （OUTREACHED / FOLLOWUP_SENT 一律收敛到 FOLLOWUP_SENT）。
    """
    rec = _get_or_create_rec(user_id)
    no_timeout = followup_timeout_seconds is None or followup_timeout_seconds <= 0
    if rec.state == ProactiveState.IDLE and not no_timeout:
        rec.state = ProactiveState.OUTREACHED
    elif no_timeout:
        rec.state = ProactiveState.IDLE
    else:
        rec.state = ProactiveState.FOLLOWUP_SENT

    rec.last_outreach_ts = time.monotonic()
    rec.last_proactive_text = text
    if followup_timeout_seconds is not None:
        rec.followup_timeout_seconds = max(0.0, followup_timeout_seconds)


def reset_user_outreach(user_id: int) -> None:
    """用户发消息或档位切入静止时把状态重置回 IDLE，终结跟进节奏。"""
    rec = _get_or_create_rec(user_id)
    rec.state = ProactiveState.IDLE
    rec.last_outreach_ts = 0.0
    rec.last_proactive_text = ""
    rec.followup_timeout_seconds = 0.0


def clear_user_proactive_state(user_id: int) -> None:
    """覆盖恢复后移除从旧数据与旧连接形成的主动外联状态。"""
    _USER_PROACTIVE_STATE.pop(user_id, None)


def note_user_contact(user_id: int) -> None:
    """用户侧互动（发消息 / 戳摸）时刷新接触时间戳——被冷落问候的计时起点。"""
    _get_or_create_rec(user_id).last_user_contact_ts = time.monotonic()


def note_outreach_throttle(user_id: int) -> None:
    """刷新主动外联节流时间戳，不推进状态机、不写主动正文。

    被冷落问候在 kick 时使用：LLM 可能跳过不发言，不能走 record_user_outreach。
    """
    _get_or_create_rec(user_id).last_outreach_ts = time.monotonic()


def get_user_proactive_record(user_id: int) -> UserProactiveRecord:
    """获取用户当前的主动跟踪记录；不存在则返回默认 IDLE 记录。"""
    return _USER_PROACTIVE_STATE.get(user_id, UserProactiveRecord())
