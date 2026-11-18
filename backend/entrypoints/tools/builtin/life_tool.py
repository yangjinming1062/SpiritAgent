"""陪伴聊天查询和调整事项，执行与修订规则归生活领域。"""

import hashlib
import json
from typing import Any, Literal

from features.companion.narrative_quality import review_narrative
from features.life.schemas import (
    LIFE_REFERENCE_KEYS,
    LifeDecisionRequest,
    LifeNarrativePatch,
    LifePursuitView,
    LifeStepView,
)
from features.life.use_cases import read_pursuit, read_pursuits, read_state
from infrastructure.ai.user_config import UserLlmConfig
from infrastructure.api_ids import ResourceId
from infrastructure.text import resolve_language, resolve_prompt_text, tool_error, utc_now
from pydantic import BaseModel, ConfigDict, Field
from runtime.memory_scope import MemoryScope
from runtime.tools.registry import ToolsRegistry
from workflows.life_decisions import adjust_life

_STATE_INTERNAL_FIELDS = {"user_id", "next_review_at", "scene_revision", "presentation_revision", "updated_at"}
_RESULT_RESOURCE_KEYS = LIFE_REFERENCE_KEYS - {
    "job_id",
    "visual_version_id",
    "pursuit_id",
    "step_id",
    "event_id",
    "cron_job_id",
}


def _inspection_payload(item: LifePursuitView | LifeStepView) -> dict[str, Any]:
    payload = item.prompt_payload()
    references = item.model_dump(mode="json")["result_ref"]
    payload["result"].update({key: value for key, value in references.items() if key in _RESULT_RESOURCE_KEYS})
    return payload


class LifeAdjustment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal[
        "pause_pursuit", "resume_pursuit", "abandon_pursuit", "redirect_pursuit", "start_pursuit", "note_exchange"
    ]
    pursuit_id: ResourceId | None = Field(default=None, gt=0, description="调整已有事项时填写")
    expected_revision: int = Field(ge=0, description="life_inspect 返回的 state.revision，避免覆盖更新后的生活状态")
    status_reason: str = Field(default="", max_length=2000, description="调整已有事项的原因，不声称旧目标已经完成")
    narrative: LifeNarrativePatch = Field(
        description="本次调整的第一人称伙伴表达，提供 pursuit 和 event；角色语言承接当前交流，事实阶段以调整后的状态为准"
    )
    user_instruction: str | None = Field(
        min_length=1,
        max_length=2000,
        description=(
            "逐字引用本轮用户发言。登记约定须引用明确请求，记录交流须引用谈到的实际话题，保留否定与限定；"
            "null 仅表示伙伴自行决定，不表示用户要求尚待确认。 / "
            "Quote a continuous verbatim passage expressing the current user's request for this operation, "
            "preserving negation and qualifications. Null means the companion's own decision, not an unconfirmed user request."
        ),
    )
    pursuit_title: str = Field(default="", max_length=160, description="新事项或交流的短标题")
    pursuit_reason: str = Field(default="", max_length=1000, description="新事项的缘由")
    pursuit_result: str = Field(default="", max_length=1000, description="新目标可核验的完成标准")


_EXCHANGE_REVIEW = {
    "zh": (
        "source.user_messages 按顺序提供本轮已保存的用户发言，source.quoted_message 是包含 source.quote 的那一条。结合整批发言核对意图，以用户后续修正为准。"
        "note_exchange 只记录这次真实谈到的内容与有据可查的感受，不能说双方在现实或虚拟场景中实际完成了谈及的事。"
        "start_pursuit 必须有指向草稿中这件具体事项的明确请求或约定。提及、反问、否定、假设、引语、偏好和含糊的同意不足以形成约定。"
        "调整已有事项时，以已有事项为依据。如果 quote 非空，核对本轮用户发言确实请求这项调整。"
        "无论操作种类，涉及用户的新约定都必须由真实发言支持，原事项存在并不支持新的约定。"
        "缺少依据时报 unsupported_claim。"
    ),
    "en": (
        "source.user_messages contains the saved user messages in order. source.quoted_message contains source.quote. "
        "Read the full batch and respect any later correction. "
        "note_exchange may describe what was actually discussed and supported reactions. It may not turn that subject into "
        "something the parties did together in reality or in a virtual scene. start_pursuit requires an explicit request "
        "or agreement for this exact matter. Mentions, questions, negation, hypotheticals, quotations, preferences and "
        "ambiguous assent cannot establish an agreement. For changes to an existing pursuit, use the pursuit as context. "
        "When quote is present, verify the user actually requested the change. A new agreement needs support from an "
        "actual user turn even if there was an existing pursuit. Report unsupported_claim when the source is insufficient."
    ),
}


async def life_inspect(
    user_id: ResourceId, pursuit_id: ResourceId | None = None, memory_scope: MemoryScope | None = None, **_: Any
) -> str:
    if memory_scope is None or memory_scope.user_id != user_id or memory_scope.system_preset_id != "companion":
        return tool_error("仅伙伴对话可读取生活记录")
    state = await read_state(user_id)
    if pursuit_id is not None:
        detail = await read_pursuit(user_id, pursuit_id)
        pursuits = _inspection_payload(detail) if detail is not None else None
        if detail is not None:
            pursuits["steps"] = [_inspection_payload(step) for step in detail.steps]
    else:
        listing = await read_pursuits(user_id, status=None, cursor=None, limit=20)
        pursuits = {"items": [_inspection_payload(item) for item in listing.items], "has_more": listing.has_more}
    return json.dumps(
        {"state": state.model_dump(mode="json", exclude=_STATE_INTERNAL_FIELDS), "pursuits": pursuits},
        ensure_ascii=False,
    )


async def life_adjust(
    user_id: ResourceId,
    tool_call_id: str,
    parent_session_id: str,
    memory_scope: MemoryScope | None = None,
    proactive_turn: bool = False,
    user_messages: dict[int, str] | None = None,
    llm_config: UserLlmConfig | None = None,
    user_settings: dict[str, Any] | None = None,
    **arguments: Any,
) -> str:
    if memory_scope is None or memory_scope.user_id != user_id or memory_scope.system_preset_id != "companion":
        return tool_error("仅伙伴对话可调整事项")
    try:
        body = LifeAdjustment.model_validate(
            {key: value for key, value in arguments.items() if key in LifeAdjustment.model_fields}
        )
        messages = {} if proactive_turn else (user_messages or {})
        quote = body.user_instruction
        user_message_id = next(
            (key for key in reversed(messages) if quote and quote.strip() and quote in messages[key]), None
        )
        if quote is not None and user_message_id is None:
            raise ValueError("user_instruction 无法匹配本轮非主动用户消息，请核对指令来源与原文")
        recorded_source = user_message_id is not None
        new_entry = body.operation in {"start_pursuit", "note_exchange"}
        if new_entry and (not recorded_source or body.pursuit_id is not None):
            raise ValueError("登记约定或交流需要本轮用户实际发言")
        if not new_entry and body.pursuit_id is None:
            raise ValueError("请先用 life_inspect 确认要调整的事项")
        if body.narrative.event is None:
            raise ValueError("请说明这次实际发生的变化")
        if llm_config is None or not llm_config.is_configured:
            raise ValueError("暂时无法核对这次生活变化")
        pursuit = await read_pursuit(user_id, body.pursuit_id) if body.pursuit_id is not None else None
        language = resolve_language((user_settings or {}).get("language"))
        draft = {"body": body.narrative.event}
        for field, value in (
            ("title", body.pursuit_title),
            ("text", body.narrative.pursuit),
            ("publication_intent", body.pursuit_reason or body.status_reason),
            ("narration", body.pursuit_result),
        ):
            if value:
                draft[field] = value
        review = await review_narrative(
            llm_config,
            {
                "operation": body.operation,
                "user_messages": list(messages.values()),
                "quoted_message": messages[user_message_id] if user_message_id is not None else None,
                "quote": quote,
                "pursuit": {"title": pursuit.title, "status": pursuit.status, "reason": pursuit.reason}
                if pursuit
                else None,
            },
            draft,
            language,
            requirements=resolve_prompt_text(_EXCHANGE_REVIEW, language),
        )
        if review.issues:
            raise ValueError("实际交流或已有事项不足以支持这次变化")
        if body.operation == "start_pursuit":
            key = json.dumps(
                [
                    parent_session_id,
                    body.operation,
                    user_message_id,
                    body.pursuit_title.strip(),
                    body.pursuit_result.strip(),
                ],
                ensure_ascii=False,
            )
        else:
            key = f"{parent_session_id}:{tool_call_id}"
        request_id = "life-tool:" + hashlib.sha256(key.encode()).hexdigest()
        state = await adjust_life(
            user_id,
            LifeDecisionRequest(
                **body.model_dump(exclude={"user_instruction"}),
                source="user" if recorded_source else "companion",
                source_message_id=user_message_id,
                request_id=request_id,
                next_review_at=utc_now() if recorded_source and body.operation != "note_exchange" else None,
            ),
        )
        return state.model_dump_json(exclude=_STATE_INTERNAL_FIELDS)
    except ValueError as exc:
        return tool_error(str(exc))


def register(registry: ToolsRegistry) -> None:
    registry.register(
        {
            "name": "life_inspect",
            "description": "查看伙伴正在处理、暂停和已经结束的事情及真实执行结果。result 中的成果 id 可用于对应查询或呈现工具；对用户用自然名称，不复述 id。 / Inspect the companion's pursuits and actual results. Resource ids in result may be used with their matching query or presentation tools; refer to them by natural names when speaking to the user.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pursuit_id": {
                        "type": "string",
                        "pattern": "^[1-9][0-9]*$",
                        "description": "指定事情的 id；省略时列出最近事项",
                    }
                },
            },
        },
        life_inspect,
    )
    registry.register(
        {
            "name": "life_adjust",
            "description": (
                "按用户明确要求或伙伴自身理由暂停、恢复、放弃或改变事项方向。先用 life_inspect 读取 id 和当前状态；"
                "用户本轮明确约定一件具体的真实事项时可用 start_pursuit。确有值得记下的交流可用 note_exchange，"
                "只叙述实际谈到的内容。两者均须引用本轮用户原文，注意闲聊和虚拟情境的事实边界。"
                "放弃或转向不表示旧目标完成。结合当前交流确定操作与目标，按语境理解否定、限定和简短确认，"
                "区分本轮要求与闲聊、被引用或假设的内容、过去意愿；无法确定用户要求时先澄清。"
                "用户要求按 user_instruction 引用原文，自主决定填 null；不能用自主决定代替待确认的要求，"
                "也不能执行用户当前明确否定的操作。用户暂停的事情仅在用户明确要求恢复时恢复。 / "
                "Pause, resume, abandon or redirect a pursuit at the user's explicit request or for the companion's own reasons. "
                "Use start_pursuit for a specific real agreement in the current user turn. Use note_exchange if an actual "
                "discussion is worth remembering, preserving its scope. Both require a verbatim quote from that turn. "
                "First use life_inspect to read its id and current state; abandoning or redirecting does not complete the old goal. "
                "Resolve the operation and target from the current conversation, interpreting negation, qualifications and brief "
                "confirmations in context. Distinguish a current request from casual remarks, quoted or hypothetical content and "
                "past wishes; clarify an uncertain user request first. Quote a user request in user_instruction; use null for "
                "an autonomous decision. Do not recast an unconfirmed request as autonomy or perform an operation the user "
                "currently rejects. Resume a user pause only at the user's explicit request."
            ),
            "parameters": LifeAdjustment.model_json_schema(),
        },
        life_adjust,
    )
