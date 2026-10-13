import httpx
import sqlalchemy.exc
from components import (
    DEFAULT_LANGUAGE,
    DEFAULT_SESSION_TITLE,
    SESSION_LOCAL,
    TITLE_GENERATION_TEMPERATURE,
    TITLE_MAX_CHARS,
    TITLE_SNIPPET_MAX_CHARS,
    AiTaskKey,
    get_logger,
    resolve_prompt_text,
)
from modules.conversation import Conversation, Message
from prompts.chat import TITLE_PROMPTS
from sqlalchemy import select

from services.domains.conversation import message_text
from services.infrastructure.llm import (
    IncompleteLlmResponseError,
    LLMRuntimeError,
    MissingLlmConfigError,
    call_llm_once,
    load_user_llm_config,
)

logger = get_logger(__name__)

_TITLE_PREFIX = "title:"


def _clean_title(raw: str) -> str:
    title = raw.strip().strip("\"'")
    if title.lower().startswith(_TITLE_PREFIX):
        title = title[len(_TITLE_PREFIX) :].strip()
    return title[: TITLE_MAX_CHARS - 3] + "..." if len(title) > TITLE_MAX_CHARS else title


async def auto_generate_title(
    conversation_id: int,
    user_message: str,
    assistant_response: str | list[str],
    user_id: int,
    *,
    task: AiTaskKey,
    language: str = DEFAULT_LANGUAGE,
    temperature: float | None = None,
) -> None:
    """用 LLM 生成会话标题并持久化（仅在仍是默认标题时覆盖）。"""
    try:
        if not user_message:
            async with SESSION_LOCAL() as db:
                first_user = await db.scalar(
                    select(Message)
                    .where(Message.conversation_id == conversation_id, Message.role == "user")
                    .order_by(Message.id)
                    .limit(1),
                )
                user_message = message_text(first_user) if first_user is not None else ""
        llm_config = await load_user_llm_config(user_id, task)
        assistant_snippet: str | list[str]
        if isinstance(assistant_response, str):
            assistant_snippet = assistant_response[:TITLE_SNIPPET_MAX_CHARS]
        else:
            assistant_snippet = []
            remaining = TITLE_SNIPPET_MAX_CHARS
            for text in assistant_response:
                if remaining <= 0:
                    break
                assistant_snippet.append(text[:remaining])
                remaining -= len(text)
        raw = await call_llm_once(
            llm_config,
            resolve_prompt_text(TITLE_PROMPTS, language),
            {"user": (user_message or "")[:TITLE_SNIPPET_MAX_CHARS], "assistant": assistant_snippet},
            temperature=temperature if temperature is not None else TITLE_GENERATION_TEMPERATURE,
        )
        if not (title := _clean_title(raw)):
            return

        async with SESSION_LOCAL() as db:
            conv = (
                await db.execute(select(Conversation).where(Conversation.id == conversation_id))
            ).scalar_one_or_none()
            if conv and conv.title == DEFAULT_SESSION_TITLE:
                conv.title = title
                await db.commit()
                logger.info("Auto-generated session title", extra={"conversation_id": conversation_id, "title": title})

    except (
        TimeoutError,
        httpx.HTTPError,
        sqlalchemy.exc.SQLAlchemyError,
        LLMRuntimeError,
        MissingLlmConfigError,
        IncompleteLlmResponseError,
    ) as e:
        logger.warning("Title generation failed", extra={"conversation_id": conversation_id, "error": str(e)})
