"""决定一段独处日常，或依据已有经历推进真实事项。"""

import asyncio
import json
from typing import Literal

from features.accounts.queries import lock_active_user_ids, user_is_active
from features.action_library.prompts import ACTION_DESIGN_GUIDANCE
from features.companion.narrative_quality import review_narrative
from features.companion.persona_service import onboarded_user_ids
from features.companion.prompt_runtime import load_companion_prompt_context
from features.life.prompts import LIFE_FACT_GUIDANCE, LIFE_NARRATIVE_GUIDANCE
from features.life.queries import pursuit_evidence
from features.life.schemas import (
    LifeContext,
    LifeDecisionRequest,
    LifePursuitEvidence,
    LifeStateView,
    PrivateLifeEpisode,
    PursuitDecisionFields,
    PursuitOperation,
)
from features.life.service import LifeInputError, LifeStateConflictError
from features.life.use_cases import (
    commit_decision_in_session,
    commit_private_episode_in_session,
    reserve_review_in_session,
)
from infrastructure.ai.prompt_engineer import call_llm_json
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.ai.user_config import load_user_llm_config
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from infrastructure.text import resolve_prompt_text, utc_now
from pydantic import BaseModel, ConfigDict, model_validator
from runtime.user_maintenance import is_user_in_maintenance, user_request_scope

from workflows.life_capabilities import life_capability_context
from workflows.life_execution import run_life_execution


class LifeDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    choice: Literal["keep", "private_episode", PursuitOperation]
    episode: PrivateLifeEpisode | None = None
    pursuit: PursuitDecisionFields | None = None

    @model_validator(mode="after")
    def check_choice(self) -> "LifeDecision":
        if (self.episode is not None) != (self.choice == "private_episode") or (
            (self.pursuit is not None) != (self.choice not in {"keep", "private_episode"})
        ):
            raise ValueError("只填写对应选择的内容")
        return self


_INSTRUCTIONS = {
    "zh": """你的任务是选择怎么继续自己的生活。结合 persona、人设所处情境和已有经历，找出一件此刻有具体缘由的事。可以留意自己喜欢的东西、积累一个小习惯、换一种做法、调整真实事项，也可以安静地继续过日子。
life_context.recent_events 按时间提供经过记录的经历，topic_heads 给出每个主题的最近片段。沿兴趣、问题和实际结果接续，留意事情如何影响下一次选择。不要把当前时间或重复观察原状态当作新的变化。
{life_facts}
{life_narrative}

choice=keep 只表示继续原有生活，episode 与 pursuit 均留空。例行等待、无新发现、时间变化、检查到相同结果都适合 keep。
choice=private_episode 仅在 private_episode_allowed 时选择，填写 episode。写伙伴独自在已有虚拟情境内的观察、选择、习惯或感受。它只有伙伴自己这一位参与者，不能编排与用户见面、替用户准备物品、观察用户行为或等用户回应；只可将已有证据的真实阅读或作品作为动机，本段新发生的变化仍是独处虚拟日常。继续主题时用 topic_heads 的最近片段 id，另一个真正的兴趣才开启新主题。title 是这次片段的短标题，narrative 用一到三句第一人称说清具体变化和它的缘由，activity_type 描述此刻的虚拟活动。情境或可用物品有变化时，用 current_scene 和 scene_tags 给出有依据的当前环境和完整条件。
处理真实事项时，用 choice 指定具体操作，pursuit 填写该操作的内容。考虑多个已有主题；一件事在等结果时可选择独立的主题，不因阻碍重复登记原任务。start_pursuit 需要具体动机、可核验目标和确有用途的 next_action，title 用短标题表达。可以用 episode_origin_id 将真实行动接到 topic_heads 提供的一个个人主题。用户相关事项只可承接已有真实约定，不能由人设、偏好、虚拟情境或沉默补出约定。advance_pursuit 用于添加有意义的下一步或根据新结果改变方向，complete_pursuit 需要确凿成果且无未解决步骤。暂停、恢复、放下和转向要有实际理由；用户暂停须由用户要求恢复，resume_after 未到继续等待。转向时保留旧结果归属。
pursuit_evidence.steps 提供真实步骤及其阶段。原步骤 planned、running（含 waiting）或 unknown 时，沿原请求等待核对。能力和技术状态只用来判断可行性；面向用户的 narrative.pursuit、event 和 action 用自然话写动机、真实变化或打算，省去重复等待和技术过程。登记行动要提供 narrative.action 或 next_action.narrative。当前场景和活动不通过 pursuit 修改。
execution 是真实能力和素材目录。visual.generate、scene.create、outfit.create 和 action.design 各自会制作真实素材；只在需要该成果时使用。content.read 只读已存资讯、公开资料或一次搜索得到的最多三份来源。next_action 是计划。改变 progress 或完成事项须引用提供的 evidence_event_ids，引用与目标有关的已确认成果。pursuit_evidence.events 中未绑定事项的发布记录，只有内容确实支持此目标时才能引用。已保存的一件作品不一定足以完成整个目标。只用中文输出符合 schema 的 JSON。""",
    "en": """Decide how to continue your own life. Use the persona, the established setting and past experiences to find something you have a concrete reason to do now. Notice something you enjoy, develop a habit, try a different approach, advance a real pursuit, or quietly carry on.
life_context.recent_events gives recorded experiences in time order. topic_heads gives the latest entry in each thread. Follow interests, questions and actual results, and show how one experience changes a later choice. A new time or another look at the same state is not a new development.
{life_facts}
{life_narrative}

choice=keep means carry on with no record. Leave episode and pursuit null. Use it for routine waiting, no discovery, time passing or the same status.
choice=private_episode is available only when private_episode_allowed. Fill episode for a new observation, choice, habit or feeling within the companion's established virtual setting, alone. Do not stage a meeting with the user, prepare something for them, observe their actions or wait for them. You may refer to existing evidence of actual reading or works as motivation. The new development described here remains solo and virtual. For an existing thread use its latest id from topic_heads; start a new thread only for a distinct interest. title is a short label, narrative one to three first-person sentences with the concrete change and why it matters, and activity_type the present activity within the virtual setting. When the setting or available objects change, use current_scene and scene_tags for the supported setting and its full current conditions.
For a real pursuit, set choice to the specific operation and put its details in pursuit. Consider multiple threads. Pending work leaves room for other interests but must not be resubmitted. start_pursuit needs a specific motivation, a verifiable goal, and a useful next_action, with a short pursuit_title. episode_origin_id may connect the action to a personal thread from topic_heads. Continue a user-related matter only from an actual recorded agreement. Persona, preferences, virtual scenes and silence cannot supply an agreement. Use advance_pursuit for a meaningful next action or change based on a new result, and complete_pursuit only with sufficient evidence and no unresolved steps. Give a real reason for pausing, resuming, setting aside or redirecting. A user pause requires the user's request to resume, and respect resume_after. Keep old results on the original pursuit.
pursuit_evidence.steps describes actual attempts. When a step is planned, running (including waiting) or unknown, retain the original request and wait for resolution. Capabilities and technical status establish feasibility. In narrative.pursuit, event and action describe motivation, real changes or intention naturally. Provide narrative.action or next_action.narrative for a new action. Do not use pursuit to change the current scene or activity.
execution lists available capabilities and real materials. visual.generate, scene.create, outfit.create and action.design produce real assets; use them when their deliverable is useful. content.read reads saved items, public sources, or up to three sources from one search. next_action is a plan. Changing progress or completing requires evidence_event_ids for relevant confirmed results. An unassigned publication in pursuit_evidence.events may be cited only when its content supports this goal. One saved artifact does not necessarily fulfill the goal. Output schema JSON in English only.""",
}

_CANDIDATE_REVIEW = {
    "zh": """本轮审阅的是生活选择。source.mode 为 private_episode 时，已明确属于虚拟日常的独处细节可以成立。draft.publication_intent 中的场景和物品标签须符合本段经历及已有情境。逐段核对参与者与行为指向，任何虚构的用户参与、为用户安排行动、观察用户行为或等待用户都不符合。现实活动和外部作品仍须有证据。与 topic_heads 或 recent_events 相比，只有换说法、换时间、继续相同等待或重复相同感受时，报 contradiction。
source.mode 为 pursuit 时，draft.publication_intent 是待核对的拟议行动，JSON 字段名和资源 id 属于动作参数。核对真实成果、用户约定与角色范围。用户相关安排必须有已存在的、来自真实交流的事项记录。记录中缺少用户同意、只有模拟情境或偏好时，不得据此补写约定。新内容只描述实际转折、结果或下一步目的；技术状态及重复等待不构成转折。""",
    "en": """Review a life choice. When source.mode is private_episode, ordinary solo details within the explicitly virtual setting are permitted. The setting and available objects in draft.publication_intent must be supported by the episode and established context. Check the actor and who the activity is directed at. Invented user involvement, arranging something for the user, observing their behavior or waiting on them are violations. Real activity and external results still require evidence. Report contradiction when the recent events or topic heads show only rewording, a new time, the same waiting, or the same feeling.
When source.mode is pursuit, draft.publication_intent encodes the proposed action. Treat JSON field names and resource ids as action metadata. Verify actual results, agreements and participants. A user-related arrangement needs an existing matter grounded in an actual exchange. An unconfirmed user wish, a simulated scene or a preference cannot support an agreement. Entries should express a genuine turning point, result or purpose. Technical status and repeated waiting are not turning points.""",
}

_LOCKS: dict[int, asyncio.Lock] = {}
logger = get_logger(__name__)


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
        if not set(decision.evidence_event_ids) <= {item.id for item in [*life.recent_events, *evidence.events]}:
            raise LifeInputError("只能引用本次快照提供的生活结果证据")
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
    async with SESSION_LOCAL() as db:
        if not await user_is_active(db, user_id) or user_id not in await onboarded_user_ids(db, (user_id,)):
            return {"status": "not_ready"}
        due, slot = await reserve_review_in_session(db, user_id, utc_now())
        await db.commit()
        if not due:
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
    execution = await life_capability_context(user_id)
    instructions = (
        resolve_prompt_text(_INSTRUCTIONS, context.language)
        .replace("{life_facts}", resolve_prompt_text(LIFE_FACT_GUIDANCE, context.language))
        .replace("{life_narrative}", resolve_prompt_text(LIFE_NARRATIVE_GUIDANCE, context.language))
    )
    if execution["capabilities"].get("action.design"):
        instructions += "\n\naction.design: " + resolve_prompt_text(ACTION_DESIGN_GUIDANCE, context.language)
    personal = {
        "persona": context.persona_extras,
        "topic_heads": [item.trajectory_payload() for item in life.topic_heads],
        "life_context": life.prompt_payload(),
    }
    evidence_payload = evidence.prompt_payload()
    decision = await call_llm_json(
        config,
        instructions,
        {
            **personal,
            "current_time": context.current_time,
            "private_episode_allowed": slot is not None,
            "pursuit_evidence": evidence_payload,
            "execution": execution,
        },
        schema=LifeDecision.model_json_schema(),
        schema_name="companion_life_decision",
        parse_output=LifeDecision.model_validate,
        repair_prompt="",
        preserve_choices=("choice",),
    )
    if decision.choice == "keep":
        return {"status": "kept"}
    episode = decision.episode
    pursuit = None
    if episode is not None:
        if slot is None or (
            episode.previous_episode_id is not None
            and episode.previous_episode_id not in {item.id for item in life.topic_heads}
        ):
            return {"status": "unavailable_episode"}
        draft = {"title": episode.title, "body": episode.narrative}
        setting = episode.model_dump(mode="json", include={"current_scene", "scene_tags"}, exclude_none=True)
        if setting:
            draft["publication_intent"] = json.dumps(setting, ensure_ascii=False)
        review_source = {
            "mode": "private_episode",
            "persona": context.persona_extras,
            "setting": life.state.current_scene,
            "scene_tags": life.state.scene_tags,
            "topic_heads": personal["topic_heads"],
            "recent_events": personal["life_context"]["recent_events"],
        }
    elif decision.pursuit is not None:
        pursuit = LifeDecisionRequest(
            **decision.pursuit.model_dump(), operation=decision.choice, expected_revision=life.state.revision
        )
        _validate_pursuit(pursuit, life, evidence)
        draft = _pursuit_draft(pursuit)
        review_source = {"mode": "pursuit", **personal, "pursuit_evidence": evidence_payload}
    else:
        raise LifeInputError("生活选择缺少内容")
    review = await review_narrative(
        config,
        review_source,
        draft,
        context.language,
        requirements=resolve_prompt_text(_CANDIDATE_REVIEW, context.language),
    )
    if review.issues:
        return {"status": "discarded", "choice": decision.choice}
    async with SESSION_LOCAL() as db:
        if (
            is_user_in_maintenance(user_id)
            or user_id not in await lock_active_user_ids(db, (user_id,))
            or user_id not in await onboarded_user_ids(db, (user_id,))
        ):
            return {"status": "not_ready"}
        try:
            if episode is not None and slot is not None:
                await commit_private_episode_in_session(
                    db, user_id, episode, slot=slot, expected_revision=life.state.revision
                )
            elif pursuit is not None:
                await commit_decision_in_session(db, user_id, pursuit)
        except LifeStateConflictError:
            return {"status": "state_changed"}
        await db.commit()
    if pursuit is not None and pursuit.next_action is not None:
        await run_life_execution(user_id)
    return {"status": "updated", "choice": decision.choice}
