"""引导与人工资料维护共用的登记字段和校验。"""

import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from features.memory.models import USER_PROFILE_MAX_CONTENT_CHARS

UserProfileField = Literal["user_call_name", "user_gender", "user_birthday", "user_hobbies", "user_freeform"]
_BIRTHDAY_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")


class MemoryProfileChange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    field: UserProfileField
    value: str | None
    expected_version: int = Field(ge=0)


def validate_profile_birthday(value: str | None) -> None:
    stripped = (value or "").strip()
    if not stripped:
        return
    if not _BIRTHDAY_PATTERN.fullmatch(stripped):
        raise ValueError("persona.user_birthday must be formatted as YYYY-MM-DD")
    try:
        date.fromisoformat(stripped)
    except ValueError as exc:
        raise ValueError(f"persona.user_birthday is not a real date: {stripped!r}") from exc


def validate_profile_value(field: UserProfileField, value: str | None) -> str:
    content = (value or "").strip()
    if len(content) > USER_PROFILE_MAX_CONTENT_CHARS:
        raise ValueError(f"{field} exceeds {USER_PROFILE_MAX_CONTENT_CHARS} chars")
    if field == "user_birthday":
        validate_profile_birthday(content)
    return content
