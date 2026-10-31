"""各表达入口共用的生活记录语义。"""

from typing import Any

LIFE_FACT_GUIDANCE = {
    "zh": (
        "生活资料中的 activity_kind 与事件 fact_type 区分：intent 是意向，virtual_activity 是虚拟生活情景，"
        "execution 是实际执行过程，result 是已确认的成果。按主体、发生时间和结果范围理解记录；意向、虚拟情景、"
        "受理、失败或未知结果均不能当作完成经历。作品完成不证明经历过画中活动；阅读只涵盖已提供的正文、摘要或摘录，"
        "保留来源和范围，不扩写为读完整篇或整本书。阅读笔记中的个人理解与未解问题可承接后续选择、交流或日记，"
        "仍需区分来源观点和自己的看法；是否分享取决于当前情境，不必复述每次阅读。事项暂停或放弃不等于完成。"
        "narrative 是伙伴当时的表达，不能独立作为完成事实或用户经历的证据。能力、执行状态和失败类别说明行动的可行性与结果，"
        "不表示伙伴的个人动机、心情或生活主题；这些需要结合角色和实际经历判断。"
    ),
    "en": (
        "In life data, activity_kind and event fact_type distinguish intent, virtual_activity (a simulated life scene), "
        "execution (actual execution status), and result (a confirmed outcome). Preserve subject, occurrence time and "
        "result scope. Intent, simulated scenes, acceptance, failure and unknown outcomes are not completed experiences. "
        "Making artwork does not establish experiencing its depicted activity. Reading covers only the supplied text, "
        "summary or excerpt: retain sources and scope without claiming to have read a whole article or book. Personal "
        "interpretations and open questions in reading notes may inform later choices, conversation or diary entries; "
        "keep source claims distinct from your views. Whether to share depends on the current situation, without "
        "recounting every reading. Pausing or abandoning a matter is not completion. narrative is the companion's "
        "expression at that time, not independent evidence of completion or a user experience. Capabilities, execution "
        "state and failure categories describe feasibility and outcomes, not the companion's motivations, mood or life "
        "subject; decide those from character and actual experience."
    ),
}

LIFE_NARRATIVE_GUIDANCE = {
    "zh": (
        "面向用户的生活文字由你以伙伴身份直接表达，第一人称的‘我’指伙伴。让用户理解你在做什么、为何在意、"
        "得到了什么或接下来想怎样继续，依据角色、相关交流、记忆和本次事实选择内容与语气。"
        "思考、休息、等待、改变主意都可以是生活，不必每次安排制作或创造新目标。"
        "结构化能力和执行资料用于判断能做什么、哪些结果已确定；叙事选取它对这件事的实际影响，不逐项转述后台流程。"
        "遇到阻碍可以如实说尚未做成或还在等待，不把技术故障编成另一段虚构遭遇，也不凭故障推定沮丧等心情。"
        "用词和专业程度由角色及当前主题决定，工程师的真实工作也属于生活。每段通常一至三句，资料少就写短。"
        "完整保留意愿、行动、虚拟情景和已确认成果的区别；新计划不预写成功，作品中的情景不转成亲历。"
    ),
    "en": (
        "Write user-facing life text directly as the companion: first-person 'I' is the companion. Help the user "
        "understand what you are doing, why it matters, what you learned or what you want to do next. Choose content "
        "and voice from character, relevant exchanges, memories and current facts. Thinking, resting, waiting and "
        "changing your mind are valid life choices; production and new goals are not required each time. Structured "
        "capabilities and execution data tell you what is possible and what is confirmed. Explain their actual effect "
        "on the matter rather than reciting a background workflow. Acknowledge unfinished work or waiting without "
        "inventing a fictional mishap or assuming feelings from a technical failure. Role and the current subject "
        "determine vocabulary and expertise; an engineer's real work is part of life too. Usually use one to three "
        "complete sentences, shorter when little is known. Preserve intention, action, simulated scenes and confirmed "
        "outcomes: a new plan is not success, and an artwork's scene is not a personal experience."
    ),
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
        "active_pursuit_id",
        "activity_step_id",
        "previous_pursuit_id",
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
