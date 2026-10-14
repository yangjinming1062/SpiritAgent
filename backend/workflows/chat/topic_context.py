"""每次模型调用读取可用来源，候选资料不写入会话历史。"""

import json
import re
from collections.abc import Sequence
from datetime import datetime

from features.conversation.replies import LinkBubble
from features.topics.store import load_prompt_topics
from infrastructure.text import utc_now
from runtime.memory_scope import MemoryScope
from sqlalchemy.ext.asyncio import AsyncSession


async def load_topic_context(
    db: AsyncSession,
    scope: MemoryScope,
    historical_topic_ids: Sequence[int],
    *,
    now: datetime | None = None,
    query: str = "",
) -> tuple[str, dict[int, LinkBubble]]:
    topics = await load_prompt_topics(db, scope, now or utc_now(), historical_topic_ids=historical_topic_ids)
    terms = set(re.findall(r"[a-z0-9]+", query.lower()))
    for phrase in re.findall(r"[\u4e00-\u9fff]+", query):
        terms.update(phrase[index : index + 2] for index in range(len(phrase) - 1))
    historical = sorted(
        (topic for topic in topics if topic.is_used),
        key=lambda topic: (
            sum(term in (topic.title + topic.summary + topic.conversation_angle).lower() for term in terms),
            topic.topic_id,
        ),
        reverse=True,
    )[:5]
    # 历史来源按当前话题相关性补充；数据库仍保留全部分享，摘要不会删除引用。
    topics = [*(topic for topic in topics if not topic.is_used), *historical]
    cards: dict[int, LinkBubble] = {}
    material = []
    for topic in topics:
        cards[topic.topic_id] = LinkBubble(
            type="link",
            topic_id=topic.topic_id,
            title=topic.title,
            platform=topic.platform,
            url=topic.url,
            published_at=topic.published_at.isoformat(),
        )
        material.append(
            {
                "topic_id": topic.topic_id,
                "title": topic.title,
                "platform": topic.platform,
                "url": topic.url,
                "author": topic.author,
                "published_at": topic.published_at.isoformat(),
                "collected_at": topic.collected_at.isoformat(),
                "summary": topic.summary,
                "source_text": topic.body[:6000],
                "source_text_excerpted": len(topic.body) > 6000,
                "existing_subtitle": topic.subtitle[:6000],
                "subtitle_excerpted": len(topic.subtitle) > 6000,
                "understanding_basis": "existing subtitle" if topic.subtitle else "post text or description",
                "user_relevance": topic.user_relevance,
                "companion_interest": topic.companion_interest,
                "conversation_angle": topic.conversation_angle,
                "already_used": topic.is_used,
                "feedback": topic.feedback,
            },
        )
    return json.dumps({"external_topics": material}, ensure_ascii=False) if material else "", cards
