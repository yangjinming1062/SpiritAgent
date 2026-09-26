"""拆分并缓存 onboarding 的角色外貌描述。"""

import asyncio
import json

from components import SESSION_LOCAL, get_logger, parse_llm_json
from prompts.generation import APPEARANCE_SPLIT_SYSTEM_PROMPT
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from services.domains.companion import get_or_create_persona, load_persona_definition
from services.infrastructure.llm import MissingLlmConfigError, chat

logger = get_logger(__name__)


class AppearancePreparationError(RuntimeError):
    """拆分结果不可用；此时不能继续提交图像生成。"""


class AppearanceSourceChangedError(AppearancePreparationError):
    """模型等待期间原始外貌描述已被修改。"""


class AppearanceParts(BaseModel):
    """按头像和全身画面拆分后的外貌资料。"""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    portrait_description: str = Field(max_length=1000)
    fullbody_description: str = Field(max_length=1000)


class _AppearancePartsCache(AppearanceParts):
    source_appearance: str


_PREPARATION_LOCKS: dict[int, asyncio.Lock] = {}


def _read_appearance_parts_cache(raw: str | None, source_appearance: str) -> AppearanceParts | None:
    if not raw:
        return None
    try:
        cached = _AppearancePartsCache.model_validate_json(raw)
    except (ValueError, TypeError):
        return None
    if cached.source_appearance != source_appearance:
        return None
    return AppearanceParts(
        portrait_description=cached.portrait_description,
        fullbody_description=cached.fullbody_description,
    )


async def _current_appearance(user_id: int) -> tuple[str, str]:
    async with SESSION_LOCAL() as db:
        persona = await get_or_create_persona(db, user_id)
        definition = load_persona_definition(persona)
        return str(definition.get("appearance") or "").strip(), persona.appearance_parts_json


async def _save_appearance_parts(user_id: int, source_appearance: str, parts: AppearanceParts) -> None:
    async with SESSION_LOCAL() as db:
        persona = await get_or_create_persona(db, user_id)
        await db.refresh(persona, with_for_update=True)
        current_appearance = str(load_persona_definition(persona).get("appearance") or "").strip()
        if current_appearance != source_appearance:
            raise AppearanceSourceChangedError("角色形象描述已更新，请重新开始生成")
        cache = _AppearancePartsCache(
            source_appearance=source_appearance,
            portrait_description=parts.portrait_description,
            fullbody_description=parts.fullbody_description,
        )
        persona.appearance_parts_json = cache.model_dump_json()
        await db.commit()


async def prepare_appearance_parts(
    user_id: int,
    *,
    expected_appearance: str | None = None,
) -> AppearanceParts | None:
    """按需准备当前原始描述；成功缓存后，头像和全身共用同一结果。"""
    lock = _PREPARATION_LOCKS.setdefault(user_id, asyncio.Lock())
    async with lock:
        source_appearance, cached_raw = await _current_appearance(user_id)
        expected = (expected_appearance or "").strip()
        if expected_appearance is not None and source_appearance != expected:
            raise AppearanceSourceChangedError("角色形象描述已更新，请重新开始生成")
        if not source_appearance:
            return None
        if cached := _read_appearance_parts_cache(cached_raw, source_appearance):
            return cached

        try:
            raw = await chat(
                None,
                user_id,
                APPEARANCE_SPLIT_SYSTEM_PROMPT
                + "\n\nJSON Schema：\n"
                + json.dumps(AppearanceParts.model_json_schema(), ensure_ascii=False),
                json.dumps({"appearance": source_appearance}, ensure_ascii=False),
            )
            parts = AppearanceParts.model_validate(parse_llm_json(raw))
        except MissingLlmConfigError as exc:
            raise AppearancePreparationError("未配置文字模型，请先在设置中配置后重试") from exc
        except ValidationError as exc:
            logger.warning(
                "invalid appearance description parts",
                extra={"user_id": user_id, "fields": [error["loc"] for error in exc.errors()]},
            )
            raise AppearancePreparationError("形象描述整理结果不完整或超出限制，请重试") from exc
        except Exception as exc:
            logger.warning("appearance description preparation failed", extra={"user_id": user_id}, exc_info=True)
            raise AppearancePreparationError("形象描述暂时无法整理，请稍后重试") from exc

        await _save_appearance_parts(user_id, source_appearance, parts)
        return parts
