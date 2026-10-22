import asyncio
import json

from infrastructure.ai.prompt_engineer import call_llm_once
from infrastructure.ai.user_config import UserLlmConfig
from infrastructure.config import SETTINGS
from infrastructure.logger import get_logger
from infrastructure.text import coerce_int, tool_error
from infrastructure.web.extract_provider import resolve_extract_provider, resolve_search_provider
from infrastructure.web.web_providers.base import WebDocument, WebSearchProvider
from runtime.tools.prompts import (
    WEB_EXTRACT_DESC,
    WEB_EXTRACT_PARAM_DESCS,
    WEB_SEARCH_DESC,
    WEB_SEARCH_PARAM_DESCS,
    WEB_SUMMARY_INSTRUCTIONS,
)
from runtime.tools.registry import ToolsRegistry

logger = get_logger(__name__)

# 与 schema 的 maximum 共用；派发层只矫正参数类型，不校验数值范围。
_MAX_SEARCH_RESULTS = 100


async def _summarize_doc(llm_config: UserLlmConfig, doc: WebDocument) -> None:
    content = doc.content
    if not content or len(content) <= 1000:
        return
    try:
        summary = await call_llm_once(
            llm_config,
            WEB_SUMMARY_INSTRUCTIONS,
            {"source_url": doc.url, "content": content[:50000], "source_excerpted": len(content) > 50000},
            temperature=0.1,
        )
        if not summary.strip():
            raise RuntimeError("Web summary response was empty")
        doc.content = summary
        doc.content_kind = "summary"
        doc.source_excerpted = len(content) > 50000
    except Exception as e:
        # 单文档失败必须隔离，否则会拖垮整批 gather（httpx、JSON 解析、LLMRuntimeError、空 choices 都落在这一层）。
        logger.warning("Failed to summarize content", extra={"error_msg": str(e)})
        doc.content = content[:5000]
        doc.content_kind = "extracted_text"
        doc.source_excerpted = len(content) > 5000
        doc.summarization_failed = True


async def _summarize_documents(documents: list[WebDocument], llm_config: UserLlmConfig) -> None:
    if not documents:
        return
    # 限制并发数，避免 50 个 URL 时同时打开 50 条 LLM 流。
    sem = asyncio.Semaphore(4)

    async def _guarded(doc: WebDocument) -> None:
        async with sem:
            await _summarize_doc(llm_config, doc)

    await asyncio.gather(*(_guarded(d) for d in documents))


async def web_search_tool(query: str, limit: int | None = None, **_: object) -> str:
    provider = resolve_search_provider()
    if not provider.is_available():
        return tool_error(f"{provider.display_name} is not configured or unavailable.")
    if not provider.supports_search():
        return tool_error(f"{provider.display_name} does not support search.")

    default_limit = SETTINGS.web_search_default_results
    safe_limit = min(_MAX_SEARCH_RESULTS, max(1, coerce_int(limit, default_limit)))
    # 查询词是对话内容，INFO 日志只记供应商与条数。
    logger.info("Web search", extra={"provider_name": provider.name, "limit": safe_limit})
    try:
        result = await provider.search(query, safe_limit)
    except Exception as e:
        return tool_error(f"Search error: {e!s}")

    return result.model_dump_json(exclude_none=True)


def _extract_provider_problem(provider: WebSearchProvider) -> str | None:
    """extract 能力检查的单一来源；None 表示可用，返回值是模型可见的不可用原因。"""
    if not provider.is_available():
        return provider.missing_credential_message() or f"{provider.display_name} is not configured or unavailable."
    if not provider.supports_extract():
        return f"{provider.display_name} does not support extraction."
    return None


async def web_extract_tool(
    urls: list[str],
    llm_config: UserLlmConfig,
    use_llm_processing: bool = True,
    **_: object,
) -> str:
    provider = resolve_extract_provider()
    if (problem := _extract_provider_problem(provider)) is not None:
        return tool_error(problem)

    logger.info("Web extract", extra={"provider_name": provider.name, "url_count": len(urls)})
    try:
        documents = await provider.extract(urls)
    except Exception as e:
        return tool_error(f"Extraction error: {e!s}")

    if use_llm_processing:
        # 并行展开摘要，10 URL 提取的耗时由最慢的那一份决定，而非 10 倍叠加；摘要沿本回合任务链（注入的 llm_config）。
        await _summarize_documents(documents, llm_config)
    return json.dumps(
        {
            "success": True,
            "data": {"web": [doc.model_dump(exclude_none=True, exclude_unset=True) for doc in documents]},
        },
        ensure_ascii=False,
    )


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
                "maximum": _MAX_SEARCH_RESULTS,
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
    """与 ``web_extract_tool`` 共用 ``_extract_provider_problem``，让无法服务 extract 的供应商不暴露对应 schema。"""
    return _extract_provider_problem(resolve_extract_provider()) is None


def register(registry: ToolsRegistry) -> None:
    registry.register(WEB_SEARCH_SCHEMA, web_search_tool)
    registry.register(WEB_EXTRACT_SCHEMA, web_extract_tool, _web_extract_available)
