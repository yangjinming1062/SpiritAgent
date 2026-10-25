"""来源、使用证据与反馈的备份贡献；采集中批次不恢复执行。"""

from datetime import date, datetime, timedelta
from functools import partial
from typing import Any

from infrastructure.backup import BackupContext, BackupRows, BackupTable
from infrastructure.text import ensure_utc
from infrastructure.web.social_content import SocialContent, validate_source_url
from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from .models import CompanionTopic, TopicBatch, TopicFeedback, TopicUsage

_BATCH_MERGE_CONFLICT = "同日资讯的采集时间、可用期或完成状态不同，无法合并会话与资讯；请使用覆盖恢复或取消该类别。"
_SOURCE_MERGE_CONFLICT = "已有资讯来源的原文、发布时间或链接与备份不同，无法合并会话与资讯。"
_RESERVE_MERGE_CONFLICT = "合并会让同一天的资讯储备超过 5 条，无法合并会话与资讯。"
_BATCH_TIMES = ("started_at", "window_start", "window_end", "available_from", "expires_at")
_SOURCE_STRINGS = ("platform", "source_id", "title", "content_type", "body", "subtitle")


def _backup_datetime(value: Any) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            raise ValueError("Invalid topic backup timestamp") from None
    if not isinstance(value, datetime):
        raise ValueError("Invalid topic backup timestamp")
    return ensure_utc(value)


def _backup_date(value: Any) -> date:
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            raise ValueError("Invalid topic backup date") from None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    raise ValueError("Invalid topic backup date")


def _validate_matching_batch(existing: TopicBatch, raw: dict[str, Any]) -> None:
    if (
        raw.get("status") == "collecting"
        or existing.status == "collecting"
        or existing.status != raw.get("status")
        or any(ensure_utc(getattr(existing, field)) != _backup_datetime(raw.get(field)) for field in _BATCH_TIMES)
    ):
        raise ValueError(_BATCH_MERGE_CONFLICT)


def _source_snapshot(raw: dict[str, Any]) -> tuple[Any, ...]:
    if any(not isinstance(raw.get(field), str) for field in _SOURCE_STRINGS) or not isinstance(raw.get("url"), str):
        raise ValueError("Invalid topic source snapshot")
    if raw["platform"] not in {"x", "xiaohongshu", "douyin"}:
        raise ValueError("Invalid topic source platform")
    return (
        *[raw[field] for field in _SOURCE_STRINGS],
        _backup_datetime(raw.get("published_at")),
        validate_source_url(raw["platform"], raw["source_id"], raw["url"]),
    )


def _validate_matching_source(existing: CompanionTopic, raw: dict[str, Any]) -> None:
    saved = {field: getattr(existing, field) for field in (*_SOURCE_STRINGS, "published_at", "url")}
    if _source_snapshot(saved) != _source_snapshot(raw):
        raise ValueError(_SOURCE_MERGE_CONFLICT)


async def validate_batch_merges(db: AsyncSession, user_id: int, rows: BackupRows) -> None:
    """在真实目标账户预检固定批次与来源，空校验账户不能证明唯一键可安全复用。"""
    if not rows.get("companion_topic_batches") and not rows.get("companion_topics"):
        return
    batches: dict[str, dict[str, Any]] = {}
    dates: set[date] = set()
    for raw in rows.get("companion_topic_batches", []):
        if raw.get("system_preset_id") != "companion":
            raise ValueError("Invalid topic companion scope")
        if "id" not in raw or str(raw["id"]) in batches:
            raise ValueError("Invalid or duplicate topic backup batch ID")
        activity_date = _backup_date(raw.get("activity_date"))
        if activity_date in dates:
            raise ValueError("Duplicate topic batch date in backup")
        dates.add(activity_date)
        batches[str(raw["id"])] = raw
    existing_batches = {
        batch.activity_date: batch
        for batch in await db.scalars(
            select(TopicBatch).where(
                TopicBatch.user_id == user_id,
                TopicBatch.system_preset_id == "companion",
                TopicBatch.activity_date.in_(dates),
            ),
        )
    }
    for raw in batches.values():
        existing = existing_batches.get(_backup_date(raw["activity_date"]))
        if existing is not None:
            _validate_matching_batch(existing, raw)

    sources: dict[tuple[str, str], dict[str, Any]] = {}
    per_batch: dict[str, set[tuple[str, str]]] = {batch_id: set() for batch_id in batches}
    for raw in rows.get("companion_topics", []):
        batch_id = str(raw.get("batch_id"))
        if batch_id not in batches or raw.get("system_preset_id") != "companion":
            raise ValueError("Invalid topic source batch")
        _source_snapshot(raw)
        key = (raw["platform"], raw["source_id"])
        if key in sources and _source_snapshot(sources[key]) != _source_snapshot(raw):
            raise ValueError(_SOURCE_MERGE_CONFLICT)
        sources[key] = raw
        per_batch[batch_id].add(key)
    if any(len(keys) > 5 for keys in per_batch.values()):
        raise ValueError(_RESERVE_MERGE_CONFLICT)

    matched_sources: set[tuple[str, str]] = set()
    keys = list(sources)
    for offset in range(0, len(keys), 1000):
        matches = await db.scalars(
            select(CompanionTopic).where(
                CompanionTopic.user_id == user_id,
                CompanionTopic.system_preset_id == "companion",
                tuple_(CompanionTopic.platform, CompanionTopic.source_id).in_(keys[offset : offset + 1000]),
            ),
        )
        for topic in matches:
            key = (topic.platform, topic.source_id)
            _validate_matching_source(topic, sources[key])
            matched_sources.add(key)
    batch_counts = dict(
        (
            await db.execute(
                select(CompanionTopic.batch_id, func.count(CompanionTopic.id))
                .where(
                    CompanionTopic.user_id == user_id,
                    CompanionTopic.batch_id.in_([batch.id for batch in existing_batches.values()]),
                )
                .group_by(CompanionTopic.batch_id),
            )
        ).all(),
    )
    for batch_id, source_keys in per_batch.items():
        existing = existing_batches.get(_backup_date(batches[batch_id]["activity_date"]))
        existing_count = batch_counts.get(existing.id, 0) if existing else 0
        if existing_count + len(source_keys - matched_sources) > 5:
            raise ValueError(_RESERVE_MERGE_CONFLICT)


async def _find_usage(
    db: AsyncSession,
    _raw: dict[str, Any],
    payload: dict[str, Any],
    context: BackupContext,
) -> int | None:
    if context.mode != "merge":
        return None
    await db.flush()
    query = select(TopicUsage.id).where(
        TopicUsage.user_id == context.user_id,
        TopicUsage.system_preset_id == payload["system_preset_id"],
        TopicUsage.topic_id == payload["topic_id"],
        TopicUsage.message_id == payload["message_id"],
    )
    if payload["message_id"] is None:
        query = query.where(TopicUsage.created_at == payload["created_at"])
    return await db.scalar(query.limit(1))


def _prepare(table: str, _raw: dict[str, Any], payload: dict[str, Any], _context: BackupContext) -> None:
    if payload.get("system_preset_id") != "companion":
        raise ValueError("Invalid topic companion scope")
    if table == "companion_topic_batches":
        payload["checkpoint"] = {}
        if payload.get("status") == "collecting":
            payload["status"], payload["error"] = "failed", "恢复的采集批次不继续执行"


async def _check(
    table: str,
    db: AsyncSession,
    _raw: dict[str, Any],
    payload: dict[str, Any],
    context: BackupContext,
) -> None:
    if table == "companion_topic_batches":
        if payload.get("status") not in {"collecting", "ready", "failed"}:
            raise ValueError("Invalid topic batch state")
        if (
            ensure_utc(payload["window_end"]) - ensure_utc(payload["window_start"]) != timedelta(hours=24)
            or payload["window_end"] != payload["started_at"]
            or payload["available_from"] >= payload["expires_at"]
        ):
            raise ValueError("Invalid topic batch time window")
        if any(type(payload.get(key)) is not int or payload[key] < 0 for key in ("search_requests", "detail_requests")):
            raise ValueError("Invalid topic request counters")
    elif table == "companion_topics":
        source = SocialContent.model_validate({key: payload[key] for key in SocialContent.model_fields})
        payload["url"] = validate_source_url(source.platform, source.source_id, source.url)
        batch = await db.get(TopicBatch, payload["batch_id"])
        if (
            batch is None
            or batch.user_id != context.user_id
            or batch.system_preset_id != payload["system_preset_id"]
            or source.published_at is None
            or not source.has_evidence
            or not ensure_utc(batch.window_start) <= source.published_at < ensure_utc(batch.window_end)
        ):
            raise ValueError("Invalid topic source or batch")
    else:
        topic = await db.get(CompanionTopic, payload["topic_id"])
        if topic is None or topic.user_id != context.user_id or topic.system_preset_id != payload["system_preset_id"]:
            raise ValueError("Invalid topic feedback or usage scope")
        if table == "companion_topic_feedback" and payload.get("value") not in {"like", "less", None}:
            raise ValueError("Invalid topic feedback")


def backup_tables() -> tuple[BackupTable, ...]:
    return (
        BackupTable(
            "companion_topic_batches",
            TopicBatch,
            unique_keys=("system_preset_id", "activity_date"),
            prepare_payload=partial(_prepare, "companion_topic_batches"),
            validate_payload=partial(_check, "companion_topic_batches"),
        ),
        BackupTable(
            "companion_topics",
            CompanionTopic,
            foreign_keys={"batch_id": "companion_topic_batches"},
            unique_keys=("system_preset_id", "platform", "source_id"),
            strict_references=True,
            prepare_payload=partial(_prepare, "companion_topics"),
            validate_payload=partial(_check, "companion_topics"),
        ),
        BackupTable(
            "companion_topic_feedback",
            TopicFeedback,
            foreign_keys={"topic_id": "companion_topics"},
            unique_keys=("topic_id",),
            strict_references=True,
            prepare_payload=partial(_prepare, "companion_topic_feedback"),
            validate_payload=partial(_check, "companion_topic_feedback"),
        ),
        BackupTable(
            "companion_topic_usages",
            TopicUsage,
            foreign_keys={"topic_id": "companion_topics", "message_id": "messages"},
            find_existing=_find_usage,
            strict_references=True,
            prepare_payload=partial(_prepare, "companion_topic_usages"),
            validate_payload=partial(_check, "companion_topic_usages"),
        ),
    )
