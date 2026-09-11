import httpx
import sqlalchemy.exc
from components import (
    DEFAULT_LANGUAGE,
    DEFAULT_SESSION_TITLE,
    SESSION_LOCAL,
    TITLE_GENERATION_MAX_TOKENS,
    TITLE_GENERATION_TEMPERATURE,
    TITLE_MAX_CHARS,
    TITLE_SNIPPET_MAX_CHARS,
    get_logger,
)
from modules.conversation import Conversation
from sqlalchemy import select

from ..llm import LLMRuntimeError, build_responses_kwargs, call_with_retry, client_for_config, scale_temperature

logger = get_logger(__name__)

_TITLE_PROMPTS: dict[str, str] = {
    "zh": (
        "为以下对话生成一个简短、描述性的标题（3-7个词）。标题应概括对话的主题或意图。只返回标题文本，不要有其他内容。不要引号、结尾标点或前缀。"
    ),
    "en": (
        "Generate a short, descriptive title (3-7 words) for a conversation that starts with the "
        "following exchange. The title should capture the main topic or intent. "
        "Return ONLY the title text, nothing else. No quotes, no punctuation at the end, no prefixes."
    ),
}


def _title_prompt(language: str) -> str:
    lang = (language or "").strip().lower()
    return _TITLE_PROMPTS.get(lang, _TITLE_PROMPTS[DEFAULT_LANGUAGE])


_TITLE_PREFIX = "title:"


def _clean_title(raw: str) -> str:
    title = raw.strip().strip("\"'")
    if title.lower().startswith(_TITLE_PREFIX):
        title = title[len(_TITLE_PREFIX) :].strip()
    return title[: TITLE_MAX_CHARS - 3] + "..." if len(title) > TITLE_MAX_CHARS else title


async def auto_generate_title(
    conversation_id: int,
    user_message: str,
    assistant_response: str,
    llm_config: dict[str, str],
    language: str = DEFAULT_LANGUAGE,
    temperature: float | None = None,
    provider_name: str | None = None,
) -> None:
    """用 LLM 生成会话标题并持久化（仅在仍是默认标题时覆盖）。"""
    try:
        client = client_for_config(llm_config)
        request = build_responses_kwargs(
            model=llm_config["model_name"],
            instructions=_title_prompt(language),
            input_items=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": f"User: {(user_message or '')[:TITLE_SNIPPET_MAX_CHARS]}\n\nAssistant: {(assistant_response or '')[:TITLE_SNIPPET_MAX_CHARS]}",
                        },
                    ],
                },
            ],
            # 供应商身份优先取 llm_config 自带的链头 provider_name（正是本次实际调用的 client）；
            # 入参 provider_name 仅在 llm_config 无身份字段时兜底，避免图片回合视觉链头 ≠ 聊天链头时按错误比例换算。
            temperature=scale_temperature(
                llm_config.get("provider_name") or provider_name,
                temperature if temperature is not None else TITLE_GENERATION_TEMPERATURE,
            ),
            max_output_tokens=TITLE_GENERATION_MAX_TOKENS,
        )
        response = await call_with_retry(client, **request)
        if not (title := _clean_title(response.output_text)):
            return

        async with SESSION_LOCAL() as db:
            conv = (
                await db.execute(select(Conversation).where(Conversation.id == conversation_id))
            ).scalar_one_or_none()
            if conv and conv.title == DEFAULT_SESSION_TITLE:
                conv.title = title
                await db.commit()
                logger.info("Auto-generated session title", extra={"conversation_id": conversation_id, "title": title})

    except (TimeoutError, httpx.HTTPError, sqlalchemy.exc.SQLAlchemyError, LLMRuntimeError) as e:
        logger.warning("Title generation failed", extra={"conversation_id": conversation_id, "error": str(e)})
