"""夜间批处理共享给日记阶段的辅助：用户本地日窗口、消息清洗。

从 nightly_activity.py 抽出，避免两个夜间流水线相互依赖而循环引用。
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from components import (
    DEFAULT_LANGUAGE,
    NIGHTLY_MESSAGE_TRUNCATE_CHARS,
    TIME_NOTE_EN_HEAD,
    TIME_NOTE_ZH_HEAD,
    format_day_marker,
    format_local_date_str,
    format_time_anchor,
    safe_json_loads,
)
from modules.conversation import Message

from services.domains.conversation import UI_ONLY_SUBTYPES


def get_local_day_utc_bounds(now_utc: datetime, tz_str: str) -> tuple[datetime, datetime, datetime, str]:
    zone = ZoneInfo(tz_str)
    user_now = now_utc.astimezone(zone)
    local_start = datetime(user_now.year, user_now.month, user_now.day, 0, 0, 0, tzinfo=zone)
    local_end = local_start + timedelta(days=1)
    utc_start = local_start.astimezone(ZoneInfo("UTC"))
    utc_end = local_end.astimezone(ZoneInfo("UTC"))
    return utc_start, utc_end, user_now, user_now.strftime("%Y-%m-%d")


def is_injected_time_item(item: dict[str, str]) -> bool:
    text = (item.get("content") or "").lstrip()
    return (
        (text.startswith("--- ") and text.endswith(" ---"))
        or text.startswith(TIME_NOTE_ZH_HEAD)
        or text.startswith(TIME_NOTE_EN_HEAD)
    )


def _message_text(msg: Message) -> str:
    if msg.role == "assistant":
        return (msg.content or "").strip()
    if msg.role != "user":
        return ""
    text_content = (msg.content or "").strip()
    if getattr(msg, "content_type", "text") == "multimodal_v1":
        parsed = safe_json_loads(msg.content or "")
        if isinstance(parsed, list):
            text_content = "\n".join(
                t
                for p in parsed
                if isinstance(p, dict)
                and p.get("type") in {"input_text", "text"}
                and (t := (p.get("text") or "").strip())
            )
    return text_content


def prefilter_messages_for_nightly(
    messages: list[Message],
    *,
    user_tz: str | None = None,
    lang: str = DEFAULT_LANGUAGE,
) -> list[dict[str, str]]:
    """夜间批处理消息清洗：去掉工具帧与纯 UI 消息；日期分界与用户时刻作为独立项，不写入正文。"""
    clean: list[dict[str, str]] = []
    prev_date_key: str | None = None
    last_user_at: datetime | None = None
    for msg in messages:
        if msg.role in ("system", "tool"):
            continue
        if getattr(msg, "subtype", None) in UI_ONLY_SUBTYPES:
            continue
        text_content = _message_text(msg)
        if not text_content:
            continue
        cur_date_key = format_local_date_str(msg.created_at, user_tz, lang) if msg.created_at is not None else None
        if cur_date_key and cur_date_key != prev_date_key:
            marker_text = format_day_marker(msg.created_at, user_tz, lang)
            if marker_text:
                clean.append({"role": "user", "content": marker_text})
            prev_date_key = cur_date_key
        clean.append({"role": msg.role, "content": text_content[:NIGHTLY_MESSAGE_TRUNCATE_CHARS]})
        if msg.role == "user" and msg.created_at is not None:
            clock = format_time_anchor(msg.created_at, last_user_at, user_tz, lang)
            if clock:
                clean.append({"role": "user", "content": clock})
            last_user_at = msg.created_at
    return clean
