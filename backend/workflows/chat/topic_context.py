"""每轮刷新候选。发送时按实际模型剩余窗口安放摘要与节选，授权只包含本次送入的来源。"""

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from features.conversation.replies import LinkBubble
from features.topics.store import ranked_topic_summaries, read_saved_topics
from infrastructure.ai.token_budget import fit_text_prefix, json_tokens
from infrastructure.database import SESSION_LOCAL
from runtime.memory_scope import MemoryScope


async def load_topic_context(
    scope: MemoryScope,
    historical_topic_ids: Sequence[int],
    *,
    now: datetime,
    query: str,
    token_budget: int,
) -> list[dict[str, Any]]:
    summaries = await ranked_topic_summaries(
        scope, now, query=query, historical_topic_ids=historical_topic_ids, token_budget=token_budget
    )
    async with SESSION_LOCAL() as db:
        sources = {
            topic.topic_id: topic
            for topic in await read_saved_topics(db, scope, [int(brief["topic_id"]) for brief in summaries])
        }
    return [
        {**brief, "topic_id": source.topic_id, "source_text": source.body, "existing_subtitle": source.subtitle}
        for brief in summaries
        if (source := sources.get(int(brief["topic_id"]))) is not None
    ]


def render_topic_context(
    candidates: list[dict[str, Any]],
    token_budget: int,
) -> tuple[str, dict[int, LinkBubble], frozenset[int]]:
    # 先容纳排序后的摘要，再在入选来源之间分配余量，长原文不能挤掉后续摘要。
    metadata_room = token_budget // 2
    material = []
    for candidate in candidates:
        item = {
            **candidate,
            "topic_id": str(candidate["topic_id"]),
            "source_text": "",
            "existing_subtitle": "",
            "source_text_excerpted": True,
            "subtitle_excerpted": True,
        }
        cost = json_tokens(item) + 2
        if cost > metadata_room:
            break
        material.append(item)
        metadata_room -= cost
    remaining = token_budget - json_tokens({"external_topics": material})
    cards = {}
    first_archived_mentions = set()
    for index, item in enumerate(material):
        source = candidates[index]
        room = remaining // (len(material) - index)
        body = fit_text_prefix(source["source_text"], room // 2 if source["existing_subtitle"] else room)
        subtitle = fit_text_prefix(source["existing_subtitle"], room - json_tokens(body))
        item.update(
            source_text=body,
            existing_subtitle=subtitle,
            source_text_excerpted=len(body) < len(source["source_text"]),
            subtitle_excerpted=len(subtitle) < len(source["existing_subtitle"]),
        )
        remaining -= json_tokens(body) + json_tokens(subtitle) + 2
        topic_id = source["topic_id"]
        cards[topic_id] = LinkBubble(
            type="link",
            topic_id=topic_id,
            title=item["title"],
            platform=item["platform"],
            url=item["url"],
            published_at=item["published_at"],
        )
        if item["past_batch"] and not item["already_used"]:
            first_archived_mentions.add(topic_id)
    return (
        json.dumps({"external_topics": material}, ensure_ascii=False) if material else "",
        cards,
        frozenset(first_archived_mentions),
    )
