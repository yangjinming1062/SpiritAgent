import contextlib
from dataclasses import dataclass, field
from datetime import date, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from components import SESSION_LOCAL, get_logger, utc_now
from modules.memory import Memory
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .memory_bootstrap import resolve_user_timezone

logger = get_logger(__name__)

STATS_THRESHOLD = 10

VALID_KINDS: frozenset[str] = frozenset({"poke", "chat_turn"})

# 时区进程级缓存：统计是每事件一次的高频路径，逐次读 Memory 行纯属浪费；覆盖恢复时显式失效。
_TZ_CACHE: dict[int, str | None] = {}


@dataclass
class _DailyCounters:
    date: str
    poke: int = 0
    chat_turn: int = 0
    # 稀疏映射：仅在该小时真的发生事件时才建键，空闲日不必分配 24 项全零字典
    hour_buckets: dict[int, int] = field(default_factory=dict)


# 进程内聚合：按 docs/ARCHITECTURE.md §5「单实例语义」，本字典即事实源
_counters: dict[int, _DailyCounters] = {}


def invalidate_user_interaction_stats(user_id: int) -> None:
    _TZ_CACHE.pop(user_id, None)
    _counters.pop(user_id, None)


async def _tz_for(user_id: int) -> str | None:
    if user_id not in _TZ_CACHE:
        async with SESSION_LOCAL() as db:
            _TZ_CACHE[user_id] = await resolve_user_timezone(db, user_id)
    return _TZ_CACHE[user_id]


def _local_today(tz: str | None) -> str:
    now = utc_now()
    if tz:
        with contextlib.suppress(ZoneInfoNotFoundError, ValueError, KeyError):
            now = now.astimezone(ZoneInfo(tz))
    return now.strftime("%Y-%m-%d")


async def _get_or_seed(user_id: int, tz: str | None) -> _DailyCounters:
    # 跨用户顺带清理过期条目使字典有界；合法本地日最多偏离 UTC 一天（时区偏移 ≤ ±14h），
    # 不与调用方本地日比对——那会把地球另一端用户的当日条目误清
    utc_today = date.fromisoformat(utc_now().strftime("%Y-%m-%d"))
    valid_dates = {(utc_today + timedelta(days=d)).isoformat() for d in (-1, 0, 1)}
    for stale_uid, stale in list(_counters.items()):
        if stale.date not in valid_dates:
            _counters.pop(stale_uid, None)
    existing = _counters.get(user_id)
    today = _local_today(tz)
    if existing is not None and existing.date == today:
        return existing
    seeded = _DailyCounters(date=today)
    _counters[user_id] = seeded
    return seeded


def _compute_peak_hour(buckets: dict[int, int]) -> int | None:
    """取计数最大的最早小时；尚无活动时返回 None。"""
    best_hour: int | None = None
    best_count = 0
    for hour in range(24):
        count = buckets.get(hour, 0)
        if count > best_count:
            best_count = count
            best_hour = hour
    return best_hour


def _format_content(counters: _DailyCounters) -> str:
    peak = _compute_peak_hour(counters.hour_buckets)
    peak_str = "n/a" if peak is None else f"{peak:02d}-{(peak + 1) % 24:02d}h"
    sorted_buckets = {k: counters.hour_buckets[k] for k in sorted(counters.hour_buckets)}
    return f"{counters.date}: poke={counters.poke}, chat_turns={counters.chat_turn}; peak={peak_str}; hour_counts={sorted_buckets}"


async def _stats_memory_for(db: AsyncSession, user_id: int, date_str: str) -> Memory | None:
    # 用 first() 而非 one_or_none()：interaction_stats:* 无唯一约束，读写竞态下可能出现多行
    return (
        (
            await db.execute(
                select(Memory).where(Memory.user_id == user_id, Memory.context == f"interaction_stats:{date_str}"),
            )
        )
        .scalars()
        .first()
    )


async def _upsert_memory(user_id: int, counters: _DailyCounters) -> None:
    content = _format_content(counters)
    tags_json = '["interaction","stats","daily_summary"]'

    async with SESSION_LOCAL() as db:
        existing = await _stats_memory_for(db, user_id, counters.date)
        if existing is not None:
            existing.content = content
            existing.tags = tags_json
        else:
            db.add(
                Memory(user_id=user_id, content=content, context=f"interaction_stats:{counters.date}", tags=tags_json),
            )
        await db.commit()
    logger.info(
        "interaction_stats: daily summary written",
        extra={"user_id": user_id, "date": counters.date, "poke": counters.poke, "chat_turn": counters.chat_turn},
    )


async def read_today_summary(user_id: int, date_str: str | None = None) -> dict | None:
    """读取指定日期（默认用户本地今日）的 interaction_stats 记忆行。"""
    if date_str is None:
        date_str = _local_today(await _tz_for(user_id))
    async with SESSION_LOCAL() as db:
        mem = await _stats_memory_for(db, user_id, date_str)
        if mem is None:
            return None
        return {"context": mem.context, "content": mem.content, "date": date_str}


async def record_interaction(user_id: int, kind: str, hour: int) -> dict:
    """累加当日 kind 计数并在越过阈值时写回 Memory。

    hour 是用户本地小时（客户端上报 getHours()），日期键同为用户本地日——
    夜间反思按本地日读取，两侧口径一致；无时区行时退化为 UTC。"""
    if kind not in VALID_KINDS:
        raise ValueError(f"unknown interaction kind: {kind!r}")
    if not isinstance(hour, int) or not 0 <= hour <= 23:
        raise ValueError(f"hour must be int in [0, 23], got {hour!r}")

    counters = await _get_or_seed(user_id, await _tz_for(user_id))
    counters.hour_buckets[hour] = counters.hour_buckets.get(hour, 0) + 1
    if kind == "poke":
        counters.poke += 1
    else:
        counters.chat_turn += 1

    threshold_met = counters.poke >= STATS_THRESHOLD or counters.chat_turn >= STATS_THRESHOLD

    if threshold_met:
        await _upsert_memory(user_id, counters)

    return {"recorded": kind, "threshold_met": threshold_met, "peak_hour": _compute_peak_hour(counters.hour_buckets)}
