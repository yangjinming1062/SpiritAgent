"""各表达入口共用的生活记录语义。"""

from typing import Any

LIFE_FACT_GUIDANCE = {
    "zh": "生活资料中的 activity_kind 与事件 fact_type 区分：intent 是意向，virtual_activity 是虚拟生活情景，execution 是实际执行过程，result 是已确认的成果。按主体、发生时间和结果范围理解记录；意向、虚拟情景、受理、失败或未知结果均不能当作完成经历。作品完成不证明经历过画中活动；阅读只涵盖已提供的正文、摘要或摘录，保留来源和范围，不扩写为读完整篇或整本书。事项暂停或放弃不等于完成。",
    "en": "In life data, activity_kind and event fact_type distinguish intent, virtual_activity (a simulated life scene), execution (actual execution status), and result (a confirmed outcome). Preserve subject, occurrence time and result scope. Intent, simulated scenes, acceptance, failure and unknown outcomes are not completed experiences. Making artwork does not establish experiencing its depicted activity. Reading covers only the supplied text, summary or excerpt: retain sources and scope without claiming to have read a whole article or book. Pausing or abandoning a matter is not completion. ",
}

_INTERNAL_CONTENT_FIELDS = frozenset(
    {
        "id",
        "user_id",
        "job_id",
        "entry_id",
        "visual_id",
        "visual_version_id",
        "pursuit_id",
        "step_id",
        "event_id",
        "post_id",
        "diary_id",
        "avatar_id",
        "outfit_id",
        "scene_id",
        "action_id",
        "reading_step_id",
        "cron_job_id",
        "generation_id",
        "event_ids",
        "evidence_event_ids",
        "step_ids",
        "idempotency_key",
        "event_key",
        "request_id",
        "capability",
        "revision",
        "scene_revision",
        "presentation_revision",
    }
)


def life_content_payload(value: Any) -> Any:
    """表达与记忆只消费事实内容；引用 ID 由各入口独立提供。"""
    if isinstance(value, dict):
        return {key: life_content_payload(item) for key, item in value.items() if key not in _INTERNAL_CONTENT_FIELDS}
    if isinstance(value, list):
        return [life_content_payload(item) for item in value]
    return value
