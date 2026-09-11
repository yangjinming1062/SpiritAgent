import asyncio
import json
import random
import time
from collections.abc import Awaitable, Callable

from components import SESSION_LOCAL, get_logger
from components.user_maintenance_runtime import track_user_task
from modules.companion import Persona
from sqlalchemy import select, update

from services.llm import MissingLlmConfigError, chat, resolve_provider_chain

from .persona_service import load_persona_definition
from .personality_tagger import analyze_personality_tags

logger = get_logger(__name__)

# 强引用集合，避免 create_task 的后台任务被 GC 回收
_TASKS: set[asyncio.Task] = set()


async def drain() -> None:
    """取消并等待所有后台任务，容忍 CancelledError。"""
    if not _TASKS:
        return
    pending = list(_TASKS)
    for t in pending:
        if not t.done():
            t.cancel()
    await asyncio.gather(*pending, return_exceptions=True)


# 单次尝试超时刻意远小于 call_with_retry 的 300s 默认值：后台任务卡住不能长期占住 worker
_BG_TASK_PER_ATTEMPT_TIMEOUT = 30.0
_BG_TASK_MAX_ATTEMPTS = 3
_BG_TASK_BASE_DELAY = 5.0
_BG_TASK_MAX_DELAY = 30.0


async def _run_with_retry(label: str, persona_id: int, user_id: int, attempt: Callable[[], Awaitable[None]]) -> None:
    last_exc: BaseException | None = None
    for i in range(1, _BG_TASK_MAX_ATTEMPTS + 1):
        try:
            await asyncio.wait_for(attempt(), timeout=_BG_TASK_PER_ATTEMPT_TIMEOUT)
            return
        except TimeoutError as exc:
            last_exc = exc
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if isinstance(exc, MissingLlmConfigError):
                break  # 缺少 provider 属于配置问题，重试无意义
        if i < _BG_TASK_MAX_ATTEMPTS:
            await asyncio.sleep(
                min(_BG_TASK_MAX_DELAY, _BG_TASK_BASE_DELAY * 2 ** (i - 1)) * (0.5 + 0.5 * random.random()),
            )
    logger.warning(
        "%s failed after %d attempts for persona_id=%s user_id=%s: %s",
        label,
        _BG_TASK_MAX_ATTEMPTS,
        persona_id,
        user_id,
        last_exc,
    )


def _spawn(name: str, user_id: int, coro: Awaitable[None]) -> None:
    task = asyncio.create_task(coro, name=name)
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    track_user_task(user_id, task)


def schedule_personality_tag_refresh(persona_id: int, user_id: int) -> None:
    _spawn(f"persona-tags-{persona_id}", user_id, _refresh_personality_tags(persona_id, user_id))


async def _refresh_personality_tags(persona_id: int, user_id: int) -> None:
    async def attempt() -> None:
        # 每次尝试都重新查询 Persona，使并发 PUT 能以最新 definition_json 参与「后写者胜」；读/调用/写各持一个短会话，LLM 调用期间不占连接
        t_query = time.monotonic()
        async with SESSION_LOCAL() as db:
            persona = (await db.execute(select(Persona).where(Persona.id == persona_id))).scalar_one_or_none()
            if persona is None:
                return  # 行已消失（用户被删？），无事可做
            definition = load_persona_definition(persona)
            species = definition.get("biological_type")
            chain = await resolve_provider_chain(db, user_id, "llm")
            definition_json = persona.definition_json
        tag_provider = chain[0] if chain else None
        t_llm = time.monotonic()
        tags = await analyze_personality_tags(
            chat,
            definition_json,
            user_id=user_id,
            species=species,
            db=None,
            provider_config=tag_provider,
        )
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
            "persona-tags-timing persona_id=%s query_and_llm=%.3fs commit=%.3fs total=%.3fs n_tags=%d",
            persona_id,
            t_llm - t_query,
            t_commit - t_llm,
            t_commit - t_query,
            len(tags) if isinstance(tags, list) else -1,
        )

    await _run_with_retry("personality tag refresh", persona_id, user_id, attempt)
