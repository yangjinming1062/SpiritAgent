"""动态创作的模型决策，公共计划字段仍由 PostPlan 定义。"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, TypeAdapter

from features.posts.schemas import PostPlan


def _post_boolean(value: Any) -> bool:
    if type(value) is not bool:
        raise ValueError("post must be a boolean")
    return value


class DeclinedPost(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    post: Annotated[Literal[False], BeforeValidator(_post_boolean)]


class PlannedPost(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    post: Annotated[Literal[True], BeforeValidator(_post_boolean)]
    plan: PostPlan


POST_DECISION = TypeAdapter(DeclinedPost | PlannedPost)


def parse_post_decision(value: Any) -> PostPlan | None:
    decision = POST_DECISION.validate_python(value)
    return decision.plan if isinstance(decision, PlannedPost) else None
