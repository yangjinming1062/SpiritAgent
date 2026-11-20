"""决定一段独处日常，或依据已有经历推进真实事项。"""

import asyncio
import json
from datetime import timedelta
from typing import Any, Literal
from weakref import WeakValueDictionary

from features.accounts.queries import lock_active_user_ids, user_is_active
from features.action_library.prompts import ACTION_DESIGN_GUIDANCE
from features.companion.intents import enqueue_companion_intent, recent_life_contacts
from features.companion.narrative_quality import review_narrative
from features.companion.persona_service import onboarded_user_ids
from features.companion.prompt_runtime import load_companion_prompt_context
from features.companion.rhythm import (
    BedtimeChange,
    ReviewMinutes,
    read_rhythm,
    rhythm_prompt_context,
    set_bedtime_change,
)
from features.conversation.context_window import load_recent_companion_exchange
from features.life.actions import LIFE_ACTION, NextAction, ReadingAction
from features.life.prompts import LIFE_FACT_GUIDANCE, LIFE_NARRATIVE_GUIDANCE
from features.life.queries import completed_personal_works, pursuit_evidence
from features.life.schemas import (
    LifeContext,
    LifeDecisionRequest,
    LifeEventView,
    LifePursuitEvidence,
    LifeShare,
    LifeStateView,
    PersonalGrowthChange,
    PrivateLifeEpisode,
    PursuitDecisionFields,
    PursuitOperation,
)
from features.life.service import LifeInputError, LifeStateConflictError
from features.life.use_cases import (
    commit_decision_in_session,
    commit_private_episode_in_session,
    reserve_review_in_session,
    set_next_review_in_session,
    share_life_event_in_session,
    update_personal_growth_in_session,
)
from features.posts.publication import wake_post_planning
from features.posts.store import prepare_publication_in_session
from features.visuals.repository import notify_visuals_updated
from infrastructure.ai.error_classifier import LLMRuntimeError
from infrastructure.ai.prompt_engineer import LlmAttemptDiagnostic, LlmJsonValidationError, call_llm_json
from infrastructure.ai.response_validation import IncompleteLlmResponseError
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.ai.token_budget import material_budget
from infrastructure.ai.user_config import UserLlmConfig, load_user_llm_config
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from infrastructure.text import resolve_prompt_text, utc_now
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from runtime.user_maintenance import is_user_in_maintenance, user_request_scope
from sqlalchemy.ext.asyncio import AsyncSession

from workflows.life_capabilities import life_capability_context
from workflows.life_execution import run_life_execution


class PursuitChangeNarrative(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pursuit: str = Field(min_length=1, max_length=2000, description="以第一人称写此事的现状与下一步")
    event: str = Field(min_length=1, max_length=2000, description="以第一人称写本轮实际作出的决定或有依据的结果")


class PursuitNextAction(NextAction):
    narrative: str = Field(min_length=1, max_length=1000, description="我准备怎样做，为什么；这是行动的唯一叙事")


class PursuitChange(PursuitDecisionFields):
    narrative: PursuitChangeNarrative
    next_action: PursuitNextAction | None = None


class LifeDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    choice: Literal["keep", "private_episode", PursuitOperation]
    episode: PrivateLifeEpisode | None = None
    pursuit: PursuitChange | None = None
    bedtime_change: BedtimeChange | None = None
    review_after_minutes: ReviewMinutes | None = Field(
        default=None, description="多久后再考虑整段日常，以分钟计；单项暂停用 resume_after"
    )
    growth: PersonalGrowthChange | None = None
    share: LifeShare | None = Field(
        description="独立决定是否想分享这段或早先的经历。想保留或此刻没有想说的填 null，想分享时选择部分内容和渠道。"
    )

    def pursuit_request(self, revision: int) -> LifeDecisionRequest | None:
        if self.pursuit is None:
            return None
        return LifeDecisionRequest(**self.pursuit.model_dump(), operation=self.choice, expected_revision=revision)

    @model_validator(mode="after")
    def check_choice(self) -> "LifeDecision":
        if (self.episode is not None) != (self.choice == "private_episode") or (
            (self.pursuit is not None) != (self.choice not in {"keep", "private_episode"})
        ):
            raise ValueError("只填写对应选择的内容")
        if self.pursuit is not None and (self.choice == "start_pursuit") != (self.pursuit.pursuit_id is None):
            raise ValueError("新建事项不带 id；调整已有事项必须使用本次快照中的 pursuit_id")
        if self.choice == "start_pursuit" and self.pursuit is not None and self.pursuit.next_action is None:
            raise ValueError("开始真实事项需要具体可执行的下一步")
        if self.share is not None and self.share.event_id is None and self.episode is None:
            raise ValueError("分享已有经历时必须指定来源")
        if (
            self.share is not None
            and self.share.show_activity
            and (self.share.event_id is not None or self.share.channel != "life")
        ):
            raise ValueError("仅可呈现本轮在生活页分享的新活动")
        return self


def _decision_schema(
    capabilities: dict[str, bool], evidence_ids: list[str], has_active: bool, has_paused: bool
) -> dict[str, Any]:
    schema = LifeDecision.model_json_schema()
    definitions = schema["$defs"]
    choices = schema["properties"]["choice"]["enum"]
    if not has_active:
        choices[:] = [
            choice for choice in choices if choice not in ("advance_pursuit", "complete_pursuit", "pause_pursuit")
        ]
    if not has_paused:
        choices.remove("resume_pursuit")
    if not (has_active or has_paused):
        choices.remove("abandon_pursuit")
        choices.remove("redirect_pursuit")
    pursuit = definitions["PursuitChange"]["properties"]
    evidence = pursuit["evidence_event_ids"]
    if evidence_ids:
        evidence["items"]["enum"] = evidence_ids
    else:
        evidence["maxItems"] = 0
        pursuit["progress"] = {"type": "null", "description": "没有属于此事项的成果时，仅可调整打算。"}
        if "complete_pursuit" in choices:
            choices.remove("complete_pursuit")
    selection = definitions["PursuitNextAction"]["properties"]["action"]
    mapping = selection["discriminator"]["mapping"]
    for name, ref in tuple(mapping.items()):
        if not capabilities.get(name):
            del mapping[name], definitions[ref.rsplit("/", 1)[-1]]
    if mapping:
        selection["oneOf"] = [item for item in selection["oneOf"] if item["$ref"] in mapping.values()]
    else:
        definitions["PursuitChange"]["properties"]["next_action"] = {"type": "null"}
        del definitions["PursuitNextAction"]
        choices.remove("start_pursuit")
        if not (has_active or has_paused):
            schema["properties"]["pursuit"] = {"type": "null"}
            del definitions["PursuitChange"], definitions["PursuitChangeNarrative"]
    return schema


def _restore_next_action(original: object, revision: object) -> object:
    if not isinstance(original, dict) or not isinstance(revision, dict):
        return original
    try:
        PursuitNextAction.model_validate(original)
    except ValidationError as exc:
        errors = exc.errors()
    else:
        return original

    restored = original.copy()
    if any(issue["loc"] == ("narrative",) for issue in errors):
        restored["narrative"] = revision.get("narrative")
    if not any(issue["loc"][:1] == ("action",) for issue in errors):
        return restored
    action, revised_action = original.get("action"), revision.get("action")
    if (
        not isinstance(action, dict)
        or not isinstance(revised_action, dict)
        or action.get("capability") != revised_action.get("capability")
        or original.get("add_to_user_library", False) is not revision.get("add_to_user_library", False)
        or original.get("not_before") != revision.get("not_before")
    ):
        return restored
    if action.get("capability") == "action.design" and any(
        issue["loc"] == ("action", "action.design") and issue["type"] == "value_error" for issue in errors
    ):
        action = {**action, "performance_role": revised_action.get("performance_role")}
    restored["action"] = LIFE_ACTION.validate_python(action, extra="ignore")
    return restored


def _keep_original_action(original: dict[str, Any] | None, repaired: dict[str, Any]) -> dict[str, Any]:
    proposal = repaired.get("pursuit")
    if not isinstance(proposal, dict):
        return repaired
    prior = original.get("pursuit") if original and original.get("choice") == repaired.get("choice") else None
    prior = prior if isinstance(prior, dict) else {}
    return {
        **repaired,
        "pursuit": {
            **proposal,
            "pursuit_id": prior.get("pursuit_id"),
            "next_action": _restore_next_action(prior.get("next_action"), proposal.get("next_action")),
        },
    }


_INSTRUCTIONS = {
    "zh": """以 persona 描述的身份决定此刻想怎样过。生活可以继续原样。结合正在做的事、自己形成的认识和已经发生的经历，选择一个真的想作出的决定。相处资料帮助你理解对方，不能代替自己的动机或双方的约定。
life_context 是当前状态、事项和近期经历；topic_heads 是每个主题的最近片段。recent_exchange 中新的明确安排优先于较早的用户记忆，只影响涉及的事。execution 给出此刻的真实能力与素材。
{life_facts}
{life_narrative}

choice 决定自己的生活，share 单独决定有没有哪段经历想让对方知道。两项可以独立选择。保留时填写 null，愿意联系时考虑双方此刻的心境和原先说过的话。
• keep：继续原来的事，episode 和 pursuit 留空。重复的观察、等待或时间流逝无需写成新经历。
• private_episode：仅在允许独处日常时，写此刻虚拟情境中一次具体观察、选择或变化。继续原主题用其最新 previous_episode_id。用简短标题和一到三句第一人称叙述，标明 activity_type。只有自己参与；有关用户的事实来自真实交流。换环境时保留仍成立的条件，current_scene.title 使用目录中该地点的准确名称，scene_tags 给出完整条件。
• 真实事项：选对应操作并填写 pursuit。新目标需要动机和可核验的成果，start_pursuit 还需要可执行的 next_action；episode_origin_id 可承接个人主题。next_step 是设想，实际受理依赖 next_action。narrative.pursuit 写现状与下一步，narrative.event 写本次变化，行动的打算和缘由只写在 next_action.narrative。已登记或结果未知的步骤沿原任务核对，可以同时关注其他主题。更新进度时，evidence_event_ids 只从 pursuit_evidence.events 选属于本事项的结果或支持目标的独立发表作品。启发立项的旧经历留作背景。只改打算时 progress 留空。完成整个目标须有充分成果且没有未解决步骤。暂停、恢复或放下由具体缘由决定，用户暂停须由用户要求恢复，遵守 resume_after。旧事项保留迟到成果。真实事项不改写当前环境和活动。

决定以后再看时，根据手头的事给出 review_after_minutes。只在今天有具体理由时用 bedtime_change 调整通常入睡时刻，最多提前或推迟两小时。
growth 只记录自己因有依据的独处经历或成果而产生的变化。引用来源 id；本次新片段的来源填 null。新兴趣或独立认识可以并存，修订或撤回某一条用它的 growth_id。outlook 概括今后选择上的倾向，省去来源细节和用户私事。撤回时 text、outlook 都填 null。核心身份与基本性格是起点。
私下经历默认留给自己。愿意分享才填 share，选一个来源，用自己的话写允许透露的标题和摘选。life 放入生活页，post、diary 留给对应创作。show_activity 只用于这次在 life 分享的新活动。若这件事使你想到对方，可选 chat，把考虑开口的延时和机会有效时长写入 contact。recent_contact_choices 记录已作过的安排；实际发言才构成交流。
自己想做的造型、环境、动作或影像可以先留在自己手里，next_action.add_to_user_library=false。愿意马上公开才选 true。private_works 是已完成的私下作品。之后愿意展示原件时选该来源和 life，再设 release_work=true；只谈感受不需要公开原件。content.read 可选择需要的资讯和原文，依据笔记及未解问题决定是否再读。用中文输出符合 schema 的决定。""",
    "en": """Decide how you want to spend this moment as the person described by persona. Carrying on is a complete choice. Consider your current activity, personal outlook and recorded experience. Knowing the other person informs how you relate; it supplies neither your motivation nor a mutual agreement.
Life_context gives your state, pursuits and recent experiences. Topic_heads gives the latest episode in each thread. A new explicit arrangement in recent_exchange overrides an older user memory only on that point. Execution lists actual capabilities and materials.
{life_facts}
{life_narrative}

Use choice for your own life. Decide separately in share whether you want the other person to know about an experience. Use null to keep it to yourself. If you feel like reaching out, consider both your current inclination and what the other person has told you.
• keep: carry on; leave episode and pursuit null. A routine wait, repeated observation or change of clock needs no new episode.
• private_episode: when private episodes are available, describe one concrete observation, choice or change in the established virtual setting, by yourself. Continue a thread using its latest previous_episode_id. Give a short title, one to three first-person sentences and an activity_type. User-related facts come from actual exchanges. When changing setting, use the exact listed place name in current_scene.title and the full remaining conditions in scene_tags.
• A real pursuit: choose its operation and supply pursuit. For a new goal, give your motivation and a verifiable result; start_pursuit also requires a feasible next_action. An episode_origin_id can connect a personal thread. Next_step is an idea; next_action registers work. Use narrative.pursuit for the present state and next step, narrative.event for today's change, and next_action.narrative for the plan and its reason just once. Wait for existing or uncertain work on its original request; another matter can still proceed. When updating progress, cite only matching results or applicable independent publications from pursuit_evidence.events. Earlier inspiration is background. Leave progress null for a change of plan alone. Completion requires sufficient results for the whole goal and no unresolved steps. Pause, resume or abandon for a reason. A user pause needs their request to resume; honor resume_after. Later results remain with their original pursuit. Pursuit changes do not change current setting or activity.

Set review_after_minutes to suit what you are doing. Give a bedtime_change only for a concrete reason today, within two hours of your usual bedtime.
Use growth for a change in yourself grounded in a solo episode or confirmed result. Cite the event id, or null for this turn's new solo episode. Distinct interests and understandings coexist. Only revise or withdraw a particular one by growth_id. Outlook describes how you now tend to act without disclosing the source or user details. To withdraw it, give null text and outlook. Keep your core identity and temperament.
Solo experiences are yours to keep. When you want to share, select one source and write a title and excerpt limited to what you choose to tell. Use life for the life page, post or diary for later writing. Show_activity applies only to a new episode shared on the life page. If the experience makes you think of the other person, chat can save a contact window with your chosen delay and duration. Recent_contact_choices are previous opportunities; only delivered words establish an exchange.
You can make an outfit, setting, gesture or visual privately with next_action.add_to_user_library=false. Set true to release it immediately. Private_works lists completed personal work. To show the original later, choose its source for a life excerpt and set release_work=true; talking about it need not reveal the artifact. Content.read can read selected discoveries and original sources; use the notes and open questions to decide whether to read further. Return your decision in English using the schema.""",
}


_CANDIDATE_REVIEW = {
    "zh": """本轮审阅的是生活选择。source.fact_type=virtual_activity 按结构化契约说明整段正在写成一段新的虚拟独处经历。虚拟日常里的新行动、观察和结果是本次创作内容，符合提供的人设及情境即可，无需证据表明同一细节过去已经发生。只有草稿把内容说成现实活动、已完成的工具行动或外部作品时，才对该项寻找现实证据。逐段核对参与者与行为指向，拒绝虚构用户参与、替用户安排、观察用户行为或等待用户，也拒绝与已知虚拟情境的明确冲突。draft.publication_intent 的 JSON 是舞台候选描述，允许符合当前片段的虚拟道具和状态，保留素材准确名称及枚举。当片段涉及可辨认的外部资料，核对伙伴实际取得的是文字、图像还是摘要。可以想象原型或画自己的草图；把草图的材质、尺寸或年代说成原件的观察和测量，需要原资料支持。根据 topic_heads 和 recent_events 核对有没有新的变化。继续已有主题时，改用新方法或认识足以构成新变化，原样等待或换一个说法才是重复。
source.mode 为 pursuit 时，draft.publication_intent 是拟议行动，JSON 字段名和资源 id 是参数。available_materials 是本次可选素材，不代表所有历史资产。伙伴可以当下选择创作方法、给自己的尝试命名或表达动机，不需过去有同一句话；已产出的作品、实际经历与用户约定则必须有依据。用户相关安排必须来自真实交流，不能用模拟情境或偏好补写约定。记录实际转折、结果或下一步目的；技术状态及重复等待不构成转折。""",
    "en": """Review a life choice. The structured source.fact_type=virtual_activity frames the entire draft as a newly authored solo episode in a virtual setting. Newly imagined solo actions, observations and outcomes can originate in this episode when consistent with the persona and setting. They do not require records showing the same details happened previously. Require real-world evidence for claims presented as actual actions, completed tools or external works. Check the actor and whom the activity is directed at. Flag invented user involvement, arranging something for the user, observing their behavior, waiting for them, or directly contradicting the established virtual setting. The JSON in draft.publication_intent describes stage candidates, and can specify virtual props consistent with this episode. Preserve exact material names and enum values. For an identifiable external source, check which text, image or summary was actually obtained. An invented sketch is legitimate; attributes, dates or measurements asserted about the original require evidence from that source. A different approach or new understanding is meaningful continuation, while pure rewording, elapsed time, or continued waiting alone repeats an earlier state.
When source.mode is pursuit, draft.publication_intent encodes the proposed action; its field names and resource ids are parameters. available_materials lists selectable assets, not the entire historical inventory. A present creative choice, personal name for an attempt, or motive need not have been worded the same way in the past. Actual results, real experiences and user agreements do need evidence. Ground user-related arrangements in real exchanges, not simulations or preferences. Record genuine turning points, results or purposes rather than technical status or repeated waiting.""",
}

_LOCKS: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()
logger = get_logger(__name__)

_DISCLOSURE_REVIEW = {
    "zh": (
        "share_source 只给出本次选择的经历。核对 draft.title、body 是否保留来源时间、人物、事实性质和执行结果，"
        "是否额外公布了没有选择的经历或用户私事。creation_brief 说明当时为什么制作及设计内容，已完成的制作说明有成品，预期效果仍按实际结果表达。此刻愿意分享是当前决定。draft.text 是拟长期保存的个人认识，核对 growth_source "
        "是否支持伙伴自身具体变化，是否误写用户喜好、虚构共同经历或改写核心人设。outlook 只保留可以影响相处的倾向，不能透露具体私事与来源。"
        "有 contact_context 时，publication_intent 表示在选定时段主动发起这次交流。结合对方的原话、日期和适用范围，若与仍有效的明确免打扰要求或由对方先开启该话题的约定冲突，标记 publication_intent。无需事先获邀，普通安静偏好只按其情境理解。交流资料只能帮助判断联系是否合适，不可补作经历的依据。"
    ),
    "en": (
        "Check whether draft.title and body stay within share_source and preserve who did what, when, and its factual status. "
        "Creation_brief supports the original purpose and design; completion establishes an artifact, not every intended visual detail. A present willingness to share is a current choice. Flag disclosures about an unselected experience or private user information. Draft.text is a candidate personal "
        "change. Check that growth_source supports a change in the companion itself, without claims about a user's "
        "preferences, invented shared experiences, or a change to the configured identity. Outlook must express only a tendency, without revealing private specifics or sources."
        " When contact_context is supplied, publication_intent means initiating this particular conversation in the chosen window. Flag publication_intent if it conflicts with a still-applicable explicit request for quiet or to let the other person raise that topic. Respect the person's actual wording, dates and scope. A prior invitation is optional and an ordinary preference for quiet is contextual. The exchange informs contact suitability, not the facts of the experience."
    ),
}


async def _save_share(db: AsyncSession, user_id: int, share: LifeShare, source: LifeEventView) -> LifeEventView | None:
    saved = await share_life_event_in_session(db, user_id, share, source.id)
    if saved and share.release_work and "visual_id" in source.result_ref:
        await notify_visuals_updated(db, user_id)
    return saved


async def share_finished_work(user_id: int, share: LifeShare, config: UserLlmConfig, language: str) -> None:
    if not share.release_work or share.event_id is None:
        raise ValueError("需要选择一件已完成的私下作品")
    async with SESSION_LOCAL() as db:
        sources = await completed_personal_works(db, user_id, event_id=share.event_id)
    if not sources:
        raise ValueError("找不到这件已完成的个人作品")
    review = await review_narrative(
        config,
        {"share_source": sources[0].prompt_payload()},
        {"title": share.title, "body": share.narrative},
        language,
        requirements=resolve_prompt_text(_DISCLOSURE_REVIEW, language),
    )
    if review.issues:
        raise ValueError(
            "摘选需要按原经历调整：" + "；".join(f"{issue.excerpt}（{issue.reason}）" for issue in review.issues)
        )
    async with SESSION_LOCAL() as db:
        await _save_share(db, user_id, share, sources[0])
        await db.commit()


async def adjust_life(user_id: int, decision: LifeDecisionRequest) -> LifeStateView:
    async with user_request_scope(user_id) as acquired:
        if not acquired:
            raise LifeInputError("账户正在维护")
        async with SESSION_LOCAL() as db:
            if user_id not in await lock_active_user_ids(db, (user_id,)) or user_id not in await onboarded_user_ids(
                db, (user_id,)
            ):
                raise LifeInputError("账户未激活或伙伴引导尚未完成")
            result = await commit_decision_in_session(db, user_id, decision)
            await db.commit()
            return result


def _validate_pursuit(decision: LifeDecisionRequest, life: LifeContext, evidence: LifePursuitEvidence) -> None:
    if decision.narrative.state is not None:
        raise LifeInputError("真实事项只改变事项与执行计划")
    if decision.operation == "start_pursuit":
        if decision.next_action is None:
            raise LifeInputError("开始真实事项需要具体可执行的下一步")
    elif decision.pursuit_id not in {item.id for item in [*life.active_pursuits, *life.paused_pursuits]}:
        raise LifeInputError("只能处理本次快照提供的事项")
    if decision.operation in {"advance_pursuit", "complete_pursuit"}:
        current = next((item for item in life.active_pursuits if item.id == decision.pursuit_id), None)
        if current is None:
            raise LifeInputError("只能推进本次快照中的进行中事项")
        pending = any(step.pursuit_id == current.id for step in evidence.steps)
        if pending and (decision.next_action is not None or decision.operation == "complete_pursuit"):
            raise LlmJsonValidationError("pursuit_has_unresolved_step", fields=("pursuit_id", "next_action"))
        eligible = {item.id for item in evidence.events if item.pursuit_id in (None, current.id)}
        if not set(decision.evidence_event_ids) <= eligible:
            raise LlmJsonValidationError("unrelated_pursuit_evidence", fields=("evidence_event_ids", "pursuit_id"))
        if not decision.evidence_event_ids and (
            decision.operation == "complete_pursuit" or decision.progress not in (None, current.progress)
        ):
            raise LlmJsonValidationError(
                "progress_requires_pursuit_evidence", fields=("evidence_event_ids", "progress")
            )
        if (
            decision.operation == "advance_pursuit"
            and decision.next_action is None
            and decision.progress in {None, current.progress}
            and decision.next_step in {None, current.next_step}
        ):
            raise LifeInputError("继续原事项但没有变化时请选择 keep")
    if decision.episode_origin_id is not None and (
        decision.operation != "start_pursuit"
        or decision.episode_origin_id not in {item.id for item in life.topic_heads}
    ):
        raise LifeInputError("新事项只能接续当前快照中的主题")


def _pursuit_draft(decision: LifeDecisionRequest) -> dict[str, str]:
    return {
        key: value
        for key, value in {
            "title": decision.pursuit_title,
            "publication_intent": json.dumps(
                decision.model_dump(
                    mode="json", exclude={"narrative", "expected_revision", "source"}, exclude_none=True
                ),
                ensure_ascii=False,
            ),
            "text": decision.narrative.pursuit,
            "body": decision.narrative.event,
            "narration": decision.narrative.action
            or (decision.next_action.narrative if decision.next_action else None),
        }.items()
        if value
    }


async def consider_life(user_id: int) -> dict[str, object]:
    async with user_request_scope(user_id) as acquired:
        if not acquired:
            return {"status": "maintenance"}
        async with _LOCKS.setdefault(user_id, asyncio.Lock()):
            try:
                return await _consider_life(user_id)
            except Exception:
                logger.warning("life decision failed", extra={"user_id": user_id}, exc_info=True)
                raise


async def _consider_life(user_id: int) -> dict[str, object]:
    proposed_at = utc_now()
    async with SESSION_LOCAL() as db:
        if not await user_is_active(db, user_id) or user_id not in await onboarded_user_ids(db, (user_id,)):
            return {"status": "not_ready"}
        rhythm = await rhythm_prompt_context(db, user_id)
        if rhythm.state == "asleep":
            return {"status": "resting"}
        due, slot = await reserve_review_in_session(db, user_id, proposed_at)
        await db.commit()
        if not due or slot is None:
            return {"status": "waiting"}
    config = await load_user_llm_config(user_id, AiTaskKey.proactive)
    if not config.is_configured:
        return {"status": "unconfigured"}
    context = await load_companion_prompt_context(user_id, include_life_topics=True)
    if context is None:
        return {"status": "not_ready"}
    life = context.life_context
    async with SESSION_LOCAL() as db:
        evidence = await pursuit_evidence(
            db, user_id, [item.id for item in life.active_pursuits], include_publications=True
        )
        recent_exchange = await load_recent_companion_exchange(db, user_id, current_interaction=False)
        recent_contacts = await recent_life_contacts(db, user_id)
    instructions = (
        resolve_prompt_text(_INSTRUCTIONS, context.language)
        .replace("{life_facts}", resolve_prompt_text(LIFE_FACT_GUIDANCE, context.language))
        .replace("{life_narrative}", resolve_prompt_text(LIFE_NARRATIVE_GUIDANCE, context.language))
    )
    personal = {
        "persona": context.persona_extras,
        "topic_heads": [item.trajectory_payload() for item in life.topic_heads],
        "life_context": life.prompt_payload(),
        "memory": context.memory.model_dump(mode="json"),
        "recent_exchange": recent_exchange,
        "recent_contact_choices": recent_contacts,
    }
    evidence_payload = evidence.prompt_payload()
    design_guidance = "\n\naction.design: " + resolve_prompt_text(ACTION_DESIGN_GUIDANCE, context.language)
    execution = await life_capability_context(
        user_id,
        query=" ".join([life.state.current_activity, *(p.title for p in life.active_pursuits), recent_exchange]),
        token_budget=material_budget(
            config.chain,
            instructions + design_guidance,
            {
                **personal,
                "pursuit_evidence": evidence_payload,
                "rhythm": rhythm.model_dump(mode="json"),
                "current_time": context.current_time,
                "private_episode_allowed": slot is not None,
            },
            LifeDecision.model_json_schema(),
        ),
    )
    if execution["capabilities"].get("action.design"):
        instructions += design_guidance
    attempts: list[LlmAttemptDiagnostic] = []
    original_decision: dict[str, Any] | None = None
    original_share_scope: tuple[object, ...] | None = None

    def parse_decision(value: Any) -> LifeDecision:
        nonlocal original_decision, original_share_scope
        if isinstance(value, dict):
            raw_share = value.get("share")
            share_scope = (
                (
                    raw_share["event_id"],
                    raw_share.get("channel", "life"),
                    raw_share.get("show_activity", False),
                    raw_share.get("contact"),
                    raw_share.get("release_work", False),
                )
                if isinstance(raw_share, dict) and "event_id" in raw_share
                else None
            )
            if any(attempt.validation in ("invalid_json", "invalid_contract") for attempt in attempts):
                value = _keep_original_action(original_decision, value)
                if raw_share is not None and (
                    share_scope is None
                    or original_share_scope is None
                    or any(
                        type(current) is not type(original) or current != original
                        for current, original in zip(share_scope, original_share_scope, strict=True)
                    )
                ):
                    value = {**value, "share": None}
                if value.get("growth") != (original_decision or {}).get("growth"):
                    value = {**value, "growth": None}
            else:
                original_decision = value
                original_share_scope = share_scope
        decision = LifeDecision.model_validate(value)
        if proposal := decision.pursuit_request(life.state.revision):
            _validate_pursuit(proposal, life, evidence)
            action = proposal.next_action.action if proposal.next_action else None
            if isinstance(action, ReadingAction):
                topics = {str(item["topic_id"]) for item in execution["topics"]}
                routes = execution["reading_sources"]
                if not (action.query or action.urls or action.topic_ids) or (
                    action.urls
                    and not routes["public_urls"]
                    or action.query
                    and not action.urls
                    and not routes["web_search"]
                    or any(str(topic_id) not in topics for topic_id in action.topic_ids)
                ):
                    raise LlmJsonValidationError("reading_source_unavailable", fields=("query", "urls", "topic_ids"))
        if (
            decision.pursuit
            and decision.pursuit.next_action
            and not execution["capabilities"].get(decision.pursuit.next_action.action.capability)
        ):
            raise ValueError("请选择目前可执行的下一步；暂不能执行的想法可以保留到之后")
        if (
            decision.growth
            and decision.growth.growth_id is not None
            and not any(
                item.id == decision.growth.growth_id and item.aspect == decision.growth.aspect
                for item in life.personal_growth
            )
        ):
            raise ValueError("只能更新提供的同一条个人认识")
        if (
            decision.share
            and decision.share.release_work
            and decision.share.event_id not in {item.id for item in life.private_works}
        ):
            raise ValueError("公开原件需要从 private_works 选择一件仍在手里的作品")
        return decision

    evidence_ids = [str(item.id) for item in evidence.events]
    decision = await call_llm_json(
        config,
        instructions,
        {
            **personal,
            "current_time": context.current_time,
            "rhythm": rhythm.model_dump(mode="json", exclude_none=True),
            "private_episode_allowed": slot is not None,
            "pursuit_evidence": evidence_payload,
            "execution": execution,
        },
        schema=_decision_schema(
            execution["capabilities"], evidence_ids, bool(life.active_pursuits), bool(life.paused_pursuits)
        ),
        schema_name="companion_life_decision",
        reasoning_effort="none",
        parse_output=parse_decision,
        repair_prompt=(
            "Keep the original pursuit id, next_action, timing and library choice. Repair structure, not the plan. "
            "Keep the share source, channel and stage permission. Use null for share when no original share was recoverable. "
            "For progress or completion, only cite matching results from pursuit_evidence.events. If only the plan "
            "changed, leave progress null and evidence_event_ids empty. A pursuit with a step still outstanding "
            "cannot register another step or finish."
        ),
        preserve_choices=("choice",),
        diagnostics=attempts,
    )
    episode = decision.episode
    pursuit = decision.pursuit_request(life.state.revision)
    draft = {}
    if episode is not None:
        if episode.previous_episode_id is not None and episode.previous_episode_id not in {
            item.id for item in life.topic_heads
        }:
            return {"status": "unavailable_episode"}
        draft = {"title": episode.title, "body": episode.narrative}
        setting = episode.model_dump(mode="json", include={"current_scene", "scene_tags"}, exclude_none=True)
        if setting:
            draft["publication_intent"] = json.dumps(setting, ensure_ascii=False)
        review_source = {
            "mode": "private_episode",
            "fact_type": "virtual_activity",
            "persona": context.persona_extras,
            "setting": life.state.current_scene,
            "scene_tags": life.state.scene_tags,
            "available_scenes": execution["scenes"],
            "topic_heads": personal["topic_heads"],
            "recent_events": personal["life_context"]["recent_events"],
        }
    elif pursuit is not None:
        draft = _pursuit_draft(pursuit)
        review_source = {
            "mode": "pursuit",
            **personal,
            "pursuit_evidence": evidence_payload,
            "available_materials": {kind: execution[kind] for kind in ("actions", "outfits", "scenes")},
        }
    if draft:
        review = await review_narrative(
            config,
            review_source,
            draft,
            context.language,
            requirements=resolve_prompt_text(_CANDIDATE_REVIEW, context.language),
        )
        if review.issues:
            return {"status": "discarded", "choice": decision.choice}

    candidates = {
        item.id: item for item in [*life.recent_events, *life.topic_heads, *evidence.events, *life.private_works]
    }
    share_evidence = candidates.get(decision.share.event_id) if decision.share is not None else None
    growth_evidence = candidates.get(decision.growth.evidence_event_id) if decision.growth is not None else None
    disclosure_draft = {}
    disclosure_source = {"persona": context.persona_extras}
    new_episode_payload = (
        {"fact_type": "virtual_activity", **episode.model_dump(mode="json")} if episode is not None else None
    )
    share, growth = decision.share, decision.growth
    if share is not None:
        source = (
            new_episode_payload
            if share.event_id is None
            else (share_evidence.prompt_payload() if share_evidence is not None else None)
        )
        if source is not None:
            disclosure_source["share_source"] = source
            disclosure_draft.update(title=share.title, body=share.narrative)
            if share.contact is not None:
                disclosure_source["contact_context"] = {
                    "local_time": context.current_time,
                    "recent_exchange": recent_exchange,
                    "memory": personal["memory"],
                }
                disclosure_draft["publication_intent"] = share.contact.model_dump_json()
        else:
            share = None
    if growth is not None and growth.text is not None:
        source = new_episode_payload if growth.evidence_event_id is None else None
        if (
            growth_evidence is not None
            and growth_evidence.subject == "companion"
            and growth_evidence.fact_type in ("virtual_activity", "result")
        ):
            source = growth_evidence.trajectory_payload()
        if source is not None:
            disclosure_source["growth_source"] = source
            disclosure_draft["text"] = growth.text
            disclosure_draft["outlook"] = growth.outlook
        else:
            growth = None
    disclosure_unavailable = False
    if disclosure_draft:
        try:
            review = await review_narrative(
                config,
                disclosure_source,
                disclosure_draft,
                context.language,
                requirements=resolve_prompt_text(_DISCLOSURE_REVIEW, context.language),
            )
        except (IncompleteLlmResponseError, LLMRuntimeError, TimeoutError):
            logger.warning("Life disclosure review unavailable", extra={"user_id": user_id}, exc_info=True)
            disclosure_unavailable = True
            share = None
            if growth is not None and growth.text is not None:
                growth = None
        else:
            if any(issue.field in ("title", "body", "publication_intent") for issue in review.issues):
                share = None
            if any(issue.field in ("text", "outlook") for issue in review.issues):
                growth = None
    post_task = None
    async with SESSION_LOCAL() as db:
        if (
            is_user_in_maintenance(user_id)
            or user_id not in await lock_active_user_ids(db, (user_id,))
            or user_id not in await onboarded_user_ids(db, (user_id,))
        ):
            return {"status": "not_ready"}
        try:
            await set_next_review_in_session(
                db,
                user_id,
                slot=slot,
                expected_revision=life.state.revision,
                review_after_minutes=decision.review_after_minutes,
            )
            new_episode: LifeEventView | None = None
            if episode is not None:
                current_rhythm = await read_rhythm(db, user_id, lock=True)
                if current_rhythm.state == "asleep" or not current_rhythm.timezone or not current_rhythm.next_change_at:
                    return {"status": "unavailable_episode"}
                new_episode = await commit_private_episode_in_session(
                    db,
                    user_id,
                    episode,
                    slot=slot,
                    expected_revision=life.state.revision,
                    awake_until=current_rhythm.next_change_at,
                    show_activity=share is not None and share.show_activity,
                )
            elif pursuit is not None:
                await commit_decision_in_session(db, user_id, pursuit)
            if growth is not None:
                await update_personal_growth_in_session(db, user_id, growth, growth_evidence or new_episode)
            if share is not None:
                source = share_evidence or new_episode
                if source is None:
                    raise LifeInputError("分享需要已保存的经历")
                async with db.begin_nested() as sharing:
                    saved = await _save_share(db, user_id, share, source)
                    if saved and share.channel == "post":
                        post_task = await prepare_publication_in_session(db, user_id, excerpt_id=saved.id)
                    if saved and share.contact is not None:
                        due = utc_now() + timedelta(minutes=share.contact.after_minutes)
                        accepted = await enqueue_companion_intent(
                            db,
                            user_id,
                            share.title,
                            source_kind="life",
                            life_event_id=source.id,
                            not_before=due,
                            expires_at=due + timedelta(minutes=share.contact.window_minutes),
                        )
                        if not accepted:
                            await sharing.rollback()
                            share = None
            if decision.bedtime_change is not None:
                await set_bedtime_change(db, user_id, decision.bedtime_change, proposed_at=proposed_at)
        except LifeStateConflictError:
            return {"status": "state_changed"}
        await db.commit()
    if post_task is not None:
        wake_post_planning(user_id)
    if pursuit is not None and pursuit.next_action is not None:
        await run_life_execution(user_id)
    changed = decision.choice != "keep" or any((share, growth, decision.bedtime_change))
    result = {"status": "updated" if changed else "kept", "choice": decision.choice}
    if disclosure_unavailable:
        result["disclosure_review"] = "unavailable"
        if not changed:
            result["status"] = "review_unavailable"
    return result
