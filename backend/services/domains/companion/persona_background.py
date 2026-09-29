import asyncio
import json
import random
import time

from components import SESSION_LOCAL, TaskBag, get_logger, track_user_task
from modules.companion import Persona
from sqlalchemy import select, update

from services.infrastructure.llm import MissingLlmConfigError, resolve_provider_config

from .persona_service import load_persona_definition
from .personality_tagger import analyze_personality_tags

logger = get_logger(__name__)

_BG = TaskBag("companion.persona_background")

# 单次尝试超时刻意远小于 call_with_retry 的 300s 默认值：后台任务卡住不能长期占住 worker
_BG_TASK_PER_ATTEMPT_TIMEOUT = 30.0
_BG_TASK_MAX_ATTEMPTS = 3
_BG_TASK_BASE_DELAY = 5.0
_BG_TASK_MAX_DELAY = 30.0


async def drain() -> None:
    """取消并等待所有后台任务，容忍 CancelledError。"""
    await _BG.drain()


def schedule_personality_tag_refresh(persona_id: int, user_id: int) -> None:
    task = asyncio.create_task(_refresh_personality_tags(persona_id, user_id), name=f"persona-tags-{persona_id}")
    _BG.add(task)
    track_user_task(user_id, task)


async def _refresh_personality_tags(persona_id: int, user_id: int) -> None:
    """失败时保留已有标签；缺少 LLM 配置不重试。"""
    last_exc: Exception | None = None
    for attempt in range(1, _BG_TASK_MAX_ATTEMPTS + 1):
        try:
            await asyncio.wait_for(_refresh_once(persona_id, user_id), timeout=_BG_TASK_PER_ATTEMPT_TIMEOUT)
            return
        except MissingLlmConfigError as exc:
            last_exc = exc
            break
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
        if attempt < _BG_TASK_MAX_ATTEMPTS:
            await asyncio.sleep(
                min(_BG_TASK_MAX_DELAY, _BG_TASK_BASE_DELAY * 2 ** (attempt - 1)) * (0.5 + 0.5 * random.random()),
            )
    logger.warning(
        "personality tag refresh failed after %d attempts for persona_id=%s user_id=%s: %s",
        _BG_TASK_MAX_ATTEMPTS,
        persona_id,
        user_id,
        last_exc,
    )


async def _refresh_once(persona_id: int, user_id: int) -> None:
    # 每次尝试都重新查询 Persona，使并发 PUT 能以最新定义参与「后写者胜」；读/调用/写各持一个短会话，LLM 调用期间不占连接
    t_query = time.monotonic()
    async with SESSION_LOCAL() as db:
        persona = await db.scalar(select(Persona).where(Persona.id == persona_id))
        if persona is None:
            return  # 行已消失（用户被删？），无事可做
        definition = load_persona_definition(persona)
        provider_config = await resolve_provider_config(db, user_id, "llm")
    t_llm = time.monotonic()
    tags = await analyze_personality_tags(definition, provider_config)
    t_write = time.monotonic()
    async with SESSION_LOCAL() as db:
        # 只更新单列：LLM 调用期间发生的 definition PUT 在其余列上仍按后写者胜生效
        await db.execute(
            update(Persona)
            .where(Persona.id == persona_id)
            .values(personality_tags_json=json.dumps(tags, ensure_ascii=False)),
        )
        await db.commit()
    t_commit = time.monotonic()
    logger.info(
        "persona-tags-timing persona_id=%s query=%.3fs llm=%.3fs commit=%.3fs total=%.3fs n_tags=%d",
        persona_id,
        t_llm - t_query,
        t_write - t_llm,
        t_commit - t_write,
        t_commit - t_query,
        len(tags),
    )
