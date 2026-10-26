"""陪伴聊天查询和调整事项，执行与修订规则归生活领域。"""

import hashlib
import json
from typing import Any, Literal

from features.life.schemas import LIFE_REFERENCE_KEYS, LifeDecisionRequest, LifePursuitView, LifeStepView
from features.life.use_cases import read_pursuit, read_pursuits, read_state
from infrastructure.api_ids import ResourceId
from infrastructure.text import tool_error, utc_now
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
    operation: Literal["pause_pursuit", "resume_pursuit", "abandon_pursuit", "redirect_pursuit"]
    pursuit_id: ResourceId = Field(gt=0)
    expected_revision: int = Field(ge=0, description="life_inspect 返回的 state.revision，避免覆盖更新后的生活状态")
    status_reason: str = Field(min_length=1, max_length=2000, description="调整的原因，不声称旧目标已经完成")
    user_instruction: str | None = Field(
        min_length=1,
        max_length=2000,
        description=(
            "用户本轮明确要求本次操作时，逐字引用能表达该要求的连续原文，保留否定与限定；"
            "null 仅表示伙伴自行决定，不表示用户要求尚待确认。 / "
            "Quote a continuous verbatim passage expressing the current user's request for this operation, "
            "preserving negation and qualifications. Null means the companion's own decision, not an unconfirmed user request."
        ),
    )
    pursuit_title: str = Field(default="", max_length=160, description="改变方向后新事项的标题")
    pursuit_reason: str = Field(default="", max_length=1000, description="新事项的缘由")
    pursuit_result: str = Field(default="", max_length=1000, description="新目标可核验的完成标准")


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
        pursuits = {
            "items": [_inspection_payload(item) for item in listing.items],
            "has_more": listing.has_more,
        }
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
    user_message: str = "",
    **arguments: Any,
) -> str:
    if memory_scope is None or memory_scope.user_id != user_id or memory_scope.system_preset_id != "companion":
        return tool_error("仅伙伴对话可调整事项")
    try:
        body = LifeAdjustment.model_validate(
            {key: value for key, value in arguments.items() if key in LifeAdjustment.model_fields}
        )
        if body.user_instruction is not None and (
            proactive_turn or not body.user_instruction.strip() or body.user_instruction not in user_message
        ):
            raise ValueError("user_instruction 无法匹配本轮非主动用户消息，请核对指令来源与原文")
        request_id = "life-tool:" + hashlib.sha256(f"{parent_session_id}:{tool_call_id}".encode()).hexdigest()
        state = await adjust_life(
            user_id,
            LifeDecisionRequest(
                **body.model_dump(exclude={"user_instruction"}),
                source="user" if body.user_instruction is not None else "companion",
                request_id=request_id,
                next_review_at=utc_now(),
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
                "放弃或转向不表示旧目标完成。结合当前交流确定操作与目标，按语境理解否定、限定和简短确认，"
                "区分本轮要求与闲聊、被引用或假设的内容、过去意愿；无法确定用户要求时先澄清。"
                "用户要求按 user_instruction 引用原文，自主决定填 null；不能用自主决定代替待确认的要求，"
                "也不能执行用户当前明确否定的操作。用户暂停的事情仅在用户明确要求恢复时恢复。 / "
                "Pause, resume, abandon or redirect a pursuit at the user's explicit request or for the companion's own reasons. "
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
