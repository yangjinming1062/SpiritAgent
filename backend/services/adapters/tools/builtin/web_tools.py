import asyncio
import json

from components import SETTINGS, coerce_int, get_logger, tool_error
from openai import AsyncOpenAI
from prompts.tools import (
    WEB_EXTRACT_DESC,
    WEB_EXTRACT_PARAM_DESCS,
    WEB_SEARCH_DESC,
    WEB_SEARCH_PARAM_DESCS,
    WEB_SUMMARY_INSTRUCTIONS,
)

from services.infrastructure.llm import UserLlmConfig, build_responses_kwargs, call_with_retry, client_for_config
from services.infrastructure.tool_runtime import ToolsRegistry
from services.infrastructure.web import resolve_extract_provider, resolve_search_provider

logger = get_logger(__name__)


async def _summarize_doc(client: AsyncOpenAI, model_name: str, doc: dict) -> None:
    content = doc.get("content", "")
    if not content or len(content) <= 1000:
        return
    try:
        request = build_responses_kwargs(
            model=model_name,
            instructions=WEB_SUMMARY_INSTRUCTIONS,
            input_items=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": json.dumps(
                                {
                                    "source_url": doc.get("url"),
                                    "content": content[:50000],
                                    "source_excerpted": len(content) > 50000,
                                },
                                ensure_ascii=False,
                            ),
                        },
                    ],
                },
            ],
            temperature=0.1,
        )
        response = await call_with_retry(client, **request)
        if response.status != "completed" or not response.output_text.strip():
            raise RuntimeError("Web summary response did not complete with text")
        doc["content"] = response.output_text
        doc["content_kind"] = "summary"
        doc["source_excerpted"] = len(content) > 50000
    except Exception as e:
        # 单文档失败必须隔离，否则会拖垮整批 gather（httpx、JSON 解析、LLMRuntimeError、空 choices 都落在这一层）。
        logger.warning("Failed to summarize content", extra={"error_msg": str(e)})
        doc["content"] = content[:5000]
        doc["content_kind"] = "extracted_text"
        doc["source_excerpted"] = len(content) > 5000
        doc["summarization_failed"] = True


async def _summarize_documents(documents: list[dict], llm_config: UserLlmConfig) -> None:
    if not documents:
        return
    model_name = llm_config.model_name
    client = client_for_config(llm_config)
    # 限制并发数，避免 50 个 URL 时同时打开 50 条 LLM 流。
    sem = asyncio.Semaphore(4)

    async def _guarded(doc: dict) -> None:
        async with sem:
            await _summarize_doc(client, model_name, doc)

    await asyncio.gather(*(_guarded(d) for d in documents))


async def web_search_tool(query: str, limit: int | None = None, **_: object) -> str:
    provider = resolve_search_provider()
    if not provider.is_available():
        return tool_error(f"{provider.display_name} is not configured or unavailable.")
    if not provider.supports_search():
        return tool_error(f"{provider.display_name} does not support search.")

    default_limit = SETTINGS.web_search_default_results
    safe_limit = max(1, coerce_int(limit, default_limit))
    # 查询词是对话内容，INFO 日志只记供应商与条数。
    logger.info("Web search", extra={"provider_name": provider.name, "limit": safe_limit})
    try:
        result = await provider.search(query, safe_limit)
    except Exception as e:
        return tool_error(f"Search error: {e!s}")

    return json.dumps(result, ensure_ascii=False)


async def web_extract_tool(
    urls: list[str],
    llm_config: UserLlmConfig,
    use_llm_processing: bool = True,
    **_: object,
) -> str:
    provider = resolve_extract_provider()
    if not provider.is_available():
        msg = provider.missing_credential_message() or (f"{provider.display_name} is not configured or unavailable.")
        return tool_error(msg)
    if not provider.supports_extract():
        return tool_error(f"{provider.display_name} does not support extraction.")

    logger.info("Web extract", extra={"provider_name": provider.name, "url_count": len(urls)})
    try:
        documents = await provider.extract(urls)
    except Exception as e:
        return tool_error(f"Extraction error: {e!s}")

    if use_llm_processing:
        # 并行展开摘要，10 URL 提取的耗时由最慢的那一份决定，而非 10 倍叠加。
        await _summarize_documents(documents, llm_config)
    # content 已是摘要或抽取原文；再附 raw_content 会把整页原文重复送入上下文。
    for doc in documents:
        doc.pop("raw_content", None)

    return json.dumps({"success": True, "data": {"web": documents}}, ensure_ascii=False)


WEB_SEARCH_SCHEMA = {
    "name": "web_search",
    "description": WEB_SEARCH_DESC,
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": WEB_SEARCH_PARAM_DESCS["query"],
            },
            "limit": {
                "type": "integer",
                "description": WEB_SEARCH_PARAM_DESCS["limit"],
                "minimum": 1,
                "maximum": 100,
            },
        },
        "required": ["query"],
    },
}

WEB_EXTRACT_SCHEMA = {
    "name": "web_extract",
    "description": WEB_EXTRACT_DESC,
    "parameters": {
        "type": "object",
        "properties": {
            "urls": {
                "type": "array",
                "items": {"type": "string"},
                "description": WEB_EXTRACT_PARAM_DESCS["urls"],
            },
            "use_llm_processing": {"type": "boolean", "description": WEB_EXTRACT_PARAM_DESCS["use_llm_processing"]},
        },
        "required": ["urls"],
    },
}


def _web_extract_available() -> bool:
    """与 ``web_extract_tool`` 运行时检查互为镜像，让无法服务 extract 的供应商不暴露对应 schema。"""
    provider = resolve_extract_provider()
    return provider.is_available() and provider.supports_extract()


def register(registry: ToolsRegistry) -> None:
    registry.register(WEB_SEARCH_SCHEMA, web_search_tool)
    registry.register(WEB_EXTRACT_SCHEMA, web_extract_tool, _web_extract_available)
