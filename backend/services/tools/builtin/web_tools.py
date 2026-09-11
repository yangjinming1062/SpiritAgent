import asyncio
import json

from components import coerce_int, get_logger, tool_error
from openai import AsyncOpenAI

from services.llm import build_responses_kwargs, call_with_retry, client_for_config
from services.tools import REGISTRY, resolve_extract_provider, resolve_search_provider

logger = get_logger(__name__)


async def _summarize_doc(client: AsyncOpenAI, model_name: str, doc: dict) -> None:
    content = doc.get("content", "")
    if not content or len(content) <= 1000:
        return
    try:
        request = build_responses_kwargs(
            model=model_name,
            instructions="Summarize the web content and extract key information in markdown format. Be concise.",
            input_items=[
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": f"URL: {doc.get('url')}\nContent: {content[:50000]}"}],
                },
            ],
            temperature=0.1,
        )
        response = await call_with_retry(client, **request)
        doc["content"] = response.output_text
    except Exception as e:
        # 单文档失败必须隔离，否则会拖垮整批 gather（httpx、JSON 解析、LLMRuntimeError、空 choices 都落在这一层）。
        logger.warning("Failed to summarize content", extra={"error_msg": str(e)})
        doc["content"] = content[:5000]


async def _summarize_documents(documents: list[dict], llm_config: dict) -> None:
    if not documents:
        return
    model_name = llm_config["model_name"]
    client = client_for_config(llm_config)
    # 限制并发数，避免 50 个 URL 时同时打开 50 条 LLM 流。
    sem = asyncio.Semaphore(4)

    async def _guarded(doc: dict) -> None:
        async with sem:
            await _summarize_doc(client, model_name, doc)

    await asyncio.gather(*(_guarded(d) for d in documents))


async def web_search_tool(query: str, limit: int = 5, **_) -> str:
    provider = resolve_search_provider()
    if not provider.is_available():
        return tool_error(f"{provider.display_name} is not configured or unavailable.")
    if not provider.supports_search():
        return tool_error(f"{provider.display_name} does not support search.")

    safe_limit = max(1, coerce_int(limit, 5))
    logger.info("Web search", extra={"provider_name": provider.name, "query": query, "limit": safe_limit})
    try:
        result = await provider.search(query, safe_limit)
    except Exception as e:
        return tool_error(f"Search error: {e!s}")

    return json.dumps(result, ensure_ascii=False)


async def web_extract_tool(urls: list[str] | str, llm_config: dict, use_llm_processing: bool = True, **_) -> str:
    if isinstance(urls, str):
        urls = [urls]
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

    # 部分供应商返回旧式 {success, data: ...} 包裹结构，这里统一拆开。
    if isinstance(documents, dict) and "data" in documents:
        documents = documents["data"]

    if use_llm_processing and isinstance(documents, list):
        # 并行展开摘要，10 URL 提取的耗时由最慢的那一份决定，而非 10 倍叠加。
        await _summarize_documents(documents, llm_config)

    return json.dumps({"success": True, "data": {"web": documents}}, ensure_ascii=False)


WEB_SEARCH_SCHEMA = {
    "name": "web_search",
    "description": 'Search the web for information. Returns up to 5 results by default with titles, URLs, and descriptions. The query is passed through to the configured backend, so operators such as site:domain, filetype:pdf, intitle:word, -term, and "exact phrase" may work when the backend supports them.',
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": 'The search query to look up on the web. You may include backend-supported operators such as site:example.com, filetype:pdf, intitle:word, -term, or "exact phrase".',
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of results to return. Defaults to 5.",
                "minimum": 1,
                "maximum": 100,
                "default": 5,
            },
        },
        "required": ["query"],
    },
}

WEB_EXTRACT_SCHEMA = {
    "name": "web_extract",
    "description": "Extract content from web page URLs. Returns page content in markdown format. Also works with PDF URLs (arxiv papers, documents, etc.) — pass the PDF link directly and it converts to markdown text. Pages under 5000 chars return full markdown; larger pages are LLM-summarized and capped at ~5000 chars per page. Pages over 2M chars are refused. If a URL fails or times out, use the browser tool to access it instead.",
    "parameters": {
        "type": "object",
        "properties": {
            "urls": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of URLs to extract content from (max 5 URLs per call)",
            },
            "format": {"type": "string", "description": "Desired format (e.g. markdown)"},
            "use_llm_processing": {"type": "boolean", "description": "Summarize content with LLM (default: true)"},
        },
        "required": ["urls"],
    },
}


def _web_extract_available() -> bool:
    """与 ``web_extract_tool`` 运行时检查互为镜像，让无法服务 extract 的供应商不暴露对应 schema。"""
    provider = resolve_extract_provider()
    return provider.is_available() and provider.supports_extract()


REGISTRY.register("web_search", WEB_SEARCH_SCHEMA, web_search_tool)
REGISTRY.register("web_extract", WEB_EXTRACT_SCHEMA, web_extract_tool, _web_extract_available)
