"""夜间模型原始输出；执行字段和副作用由编排层补充。"""

from typing import Annotated, Any, Literal

from modules.companion import DiaryContent
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, TypeAdapter

from services.infrastructure.llm import LlmJsonValidationError


class NightlyActionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,48}$")
    capability: str = Field(min_length=1)
    depends_on: list[str]
    arguments: dict[str, Any]


class NightlyPlanOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    theme: str
    rationale: str
    actions: list[NightlyActionOutput]


def _publish_boolean(value: Any) -> bool:
    if type(value) is not bool:
        raise ValueError("publish must be a boolean")
    return value


class DeclinedDiary(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    publish: Annotated[Literal[False], BeforeValidator(_publish_boolean)]


class PublishedDiary(DiaryContent):
    model_config = ConfigDict(strict=True)

    publish: Annotated[Literal[True], BeforeValidator(_publish_boolean)]


DIARY_DECISION = TypeAdapter(DeclinedDiary | PublishedDiary)


def parse_diary_decision(parsed: Any) -> DiaryContent | Literal[False]:
    decision = DIARY_DECISION.validate_python(parsed)
    if isinstance(decision, DeclinedDiary):
        return False
    return DiaryContent(title=decision.title, body=decision.body, mood=decision.mood)


class ReflectionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    content: str | None = Field(min_length=1)


def reflection_schema(max_content_chars: int) -> dict[str, Any]:
    schema = ReflectionOutput.model_json_schema()
    for variant in schema["properties"]["content"]["anyOf"]:
        if variant.get("type") == "string":
            variant["maxLength"] = max_content_chars
    return schema


def parse_reflection(parsed: Any, *, max_content_chars: int) -> str | None:
    content = ReflectionOutput.model_validate(parsed).content
    if content is not None and len(content) > max_content_chars:
        raise LlmJsonValidationError("content_too_long", fields=("content",))
    return content
