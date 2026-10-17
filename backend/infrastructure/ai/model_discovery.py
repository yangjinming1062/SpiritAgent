"""供应商模型发现与公开目录补全；目录不参与运行时选模。"""

import asyncio
import json
import time
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit

import httpx

from infrastructure.ai.config import ProviderCard
from infrastructure.ai.models import ModelDiscoveryResponse, ModelMetadata, ModelModality
from infrastructure.ai.providers.base import ServiceType
from infrastructure.ai.providers.http import get_http
from infrastructure.ai.providers.registry import configured_base_url

_CATALOG_URL = "https://openrouter.ai/api/v1"
_CATALOG_TTL = 3600.0
_CATALOG_LOCK = asyncio.Lock()
_catalog: tuple[float, list[ModelMetadata], str] | None = None
_FIELDS = ("context_window", "max_output_tokens", "input_modalities", "output_modalities")


def _positive(value: object) -> int | None:
    return (
        value if isinstance(value, int) and not isinstance(value, bool) and 0 < value <= 9_007_199_254_740_991 else None
    )


def _modalities(value: object) -> list[ModelModality] | None:
    if not isinstance(value, list):
        return None
    allowed = {item.value: item for item in ModelModality}
    return list(
        dict.fromkeys(allowed[item.lower()] for item in value if isinstance(item, str) and item.lower() in allowed),
    )


def _record(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _metadata(raw: dict, source: str, *, model_name: str | None = None) -> ModelMetadata | None:
    name = model_name or raw.get("id") or raw.get("model") or raw.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 128:
        return None
    info = _record(raw.get("model_info"))
    architecture = _record(raw.get("architecture"))
    inference = _record(raw.get("inference_metadata"))
    top = _record(raw.get("top_provider"))
    meta = _record(raw.get("meta"))
    inputs = _modalities(
        architecture.get("input_modalities", inference.get("request_modality", raw.get("input_modalities"))),
    )
    outputs = _modalities(
        architecture.get("output_modalities", inference.get("response_modality", raw.get("output_modalities"))),
    )
    methods = raw.get("supportedGenerationMethods")
    methods = methods if isinstance(methods, list) else []
    services = []
    capabilities = raw.get("capabilities")
    for capability in capabilities if isinstance(capabilities, list) else []:
        service = (
            {
                "TG": "llm",
                "Reasoning": "llm",
                "VU": "llm",
                "Multimodal-Omni": "llm",
                "ASR": "stt",
                "TTS": "tts",
                "IG": "image_gen",
                "VG": "video_gen",
                "TR": "embedding",
                "ME": "embedding",
            }.get(capability)
            if isinstance(capability, str)
            else None
        )
        if service and service not in services:
            services.append(service)
    if any(method in methods for method in ("embedContent", "batchEmbedContents")):
        services.append("embedding")
    if outputs is not None and not services:
        if ModelModality.text in outputs:
            services.append("llm")
            if inputs is not None and ModelModality.audio in inputs:
                services.append("stt")
        for modality, service in (
            (ModelModality.image, "image_gen"),
            (ModelModality.video, "video_gen"),
            (ModelModality.audio, "tts"),
            (ModelModality.embeddings, "embedding"),
        ):
            if modality in outputs:
                services.append(service)
    result = ModelMetadata(
        model_name=name.strip(),
        display_name=str(raw.get("displayName") or raw.get("name") or name),
        services=list(dict.fromkeys(services)),
        context_window=_positive(
            info.get("context_window")
            or raw.get("context_length")
            or raw.get("max_model_len")
            or raw.get("context_window"),
        ),
        max_output_tokens=_positive(
            info.get("max_output_tokens")
            or raw.get("outputTokenLimit")
            or raw.get("max_output_tokens")
            or top.get("max_completion_tokens"),
        ),
        input_modalities=inputs,
        output_modalities=outputs,
        catalog_id=name if source == "OpenRouter" else None,
        canonical_slug=str(raw.get("canonical_slug") or "") if source == "OpenRouter" else None,
        catalog_url=f"https://openrouter.ai/{name}" if source == "OpenRouter" else None,
    )
    # n_ctx_train 是训练窗口，不是本地部署的实际可用窗口。
    if source != "本地部署" and result.context_window is None:
        result.context_window = _positive(meta.get("context_length"))
    result.field_sources = {field: source for field in _FIELDS if getattr(result, field) is not None}
    return result


async def _json(
    base_url: str,
    key: str,
    path: str,
    *,
    auth_header: dict[str, str] | None = None,
    params: dict | None = None,
) -> dict:
    async with get_http(base_url, key, auth_header=auth_header).stream(
        "GET",
        path,
        params=params,
        timeout=10.0,
    ) as response:
        response.raise_for_status()
        chunks = []
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > 16 * 1024 * 1024:
                raise ValueError("模型目录响应过大")
            chunks.append(chunk)
        body = json.loads(b"".join(chunks))
    if not isinstance(body, dict):
        raise ValueError("模型目录响应格式无效")
    return body


async def _openrouter(refresh: bool) -> tuple[list[ModelMetadata], str]:
    global _catalog
    async with _CATALOG_LOCK:
        if not refresh and _catalog is not None and time.monotonic() - _catalog[0] < _CATALOG_TTL:
            return _catalog[1], _catalog[2]
        values = []
        offset = 0
        while True:
            body = await _json(
                _CATALOG_URL,
                "",
                "models",
                auth_header={},
                params={"output_modalities": "all", "offset": offset, "limit": 500},
            )
            items = body.get("data")
            if not isinstance(items, list):
                raise ValueError("OpenRouter 模型目录响应格式无效")
            for raw in items:
                if isinstance(raw, dict) and (value := _metadata(raw, "OpenRouter")) is not None:
                    values.append(value)
            if not _record(body.get("links")).get("next"):
                break
            if not items:
                raise ValueError("OpenRouter 分页没有取得新模型")
            offset += len(items)
        stamp = datetime.now(UTC).isoformat()
        _catalog = (time.monotonic(), values, stamp)
        return values, stamp


def catalog_matches(name: str, catalog: list[ModelMetadata]) -> list[ModelMetadata]:
    key = name.strip().casefold()
    exact = [item for item in catalog if item.model_name.casefold() == key]
    if exact:
        return exact
    canonical = [item for item in catalog if (item.canonical_slug or "").casefold() == key]
    if canonical:
        return canonical
    return [
        item
        for item in catalog
        if item.model_name.rsplit("/", 1)[-1].casefold() == key
        or item.display_name.rsplit(":", 1)[-1].strip().casefold() == key
    ]


def _enrich(model: ModelMetadata, suggestion: ModelMetadata) -> ModelMetadata:
    update = {
        field: getattr(suggestion, field)
        for field in _FIELDS
        if getattr(model, field) is None and getattr(suggestion, field) is not None
    }
    effective_window = model.context_window or update.get("context_window")
    if (
        effective_window is not None
        and (suggested_output := update.get("max_output_tokens")) is not None
        and suggested_output > effective_window
    ):
        update["max_output_tokens"] = effective_window
    sources = {**model.field_sources, **{field: suggestion.field_sources[field] for field in update}}
    return model.model_copy(
        update={
            **update,
            "services": model.services or suggestion.services,
            "catalog_id": suggestion.catalog_id,
            "catalog_url": suggestion.catalog_url,
            "field_sources": sources,
        },
    )


def _replace_suffix(base_url: str, suffix: str, replacement: str) -> str:
    url = urlsplit(base_url)
    path = url.path.rstrip("/")
    return urlunsplit(url._replace(path=path.removesuffix(suffix) + replacement))


async def _vendor_models(provider: ProviderCard, service: ServiceType) -> list[ModelMetadata]:
    base = configured_base_url(provider.provider_type, provider.base_url, service)
    source = "本地部署" if provider.provider_type == "local" else "供应商接口"
    auth = {"x-goog-api-key": "{api_key}"} if provider.provider_type == "gemini" else None
    if provider.provider_type == "gemini":
        endpoint, params = "v1beta/models", {"pageSize": 1000}
    elif provider.provider_type == "qwen":
        base = _replace_suffix(base, "/compatible-mode/v1", "/api/v1") if base.endswith("/compatible-mode/v1") else base
        endpoint, params = "models", {"page_no": 1, "page_size": 100}
    else:
        if provider.provider_type == "minimax" and not base.endswith("/v1"):
            base += "/v1"
        endpoint, params = "models", {}
    result = []
    pages = set()
    while True:
        page = tuple(sorted(params.items()))
        if page in pages:
            raise ValueError("供应商分页重复，无法继续取得模型")
        pages.add(page)
        body = await _json(base, provider.api_key, endpoint, auth_header=auth, params=params)
        output = _record(body.get("output"))
        items = body.get("data", body.get("models", output.get("models")))
        if not isinstance(items, list):
            raise ValueError("供应商没有返回模型列表")
        for raw in items:
            if isinstance(raw, dict):
                name = raw.get("name") if provider.provider_type == "gemini" else None
                value = _metadata(
                    raw,
                    source,
                    model_name=name.removeprefix("models/") if isinstance(name, str) else None,
                )
                if value is not None:
                    result.append(value)
        token = body.get("nextPageToken")
        if token:
            params = {**params, "pageToken": token}
        elif body.get("has_more"):
            cursor = body.get("last_id")
            if not isinstance(cursor, str) or not cursor or not items:
                raise ValueError("供应商分页缺少后续游标")
            params = {**params, "after": cursor}
        elif output.get("total", 0) > len(result):
            if not items:
                raise ValueError("供应商分页没有取得新模型")
            params = {**params, "page_no": params["page_no"] + 1}
        else:
            break
    if provider.provider_type == "grok" and service == ServiceType.llm:
        try:
            details = await _json(base, provider.api_key, "language-models")
            by_id = {
                item.model_name: item
                for raw in details.get("models", [])
                if isinstance(raw, dict) and (item := _metadata(raw, source)) is not None
            }
            result = [_enrich(item, by_id[item.model_name]) if item.model_name in by_id else item for item in result]
        except (httpx.HTTPError, ValueError):
            pass  # /models 已成功；详情端点缺失时仍返回可手动补全的列表。
    return list({item.model_name: item for item in result}.values())


async def discover_models(
    provider: ProviderCard,
    service: ServiceType,
    *,
    model_name: str | None,
    catalog_id: str | None,
    refresh: bool,
) -> ModelDiscoveryResponse:
    response = ModelDiscoveryResponse()
    if provider.provider_type == "local" and service == ServiceType.image_gen:
        response.models = [
            ModelMetadata(
                model_name="qwen",
                display_name="固定 Qwen 工作流",
                services=["image_gen"],
                input_modalities=[ModelModality.text, ModelModality.image],
                output_modalities=[ModelModality.image],
            ),
        ]
        return response
    try:
        async with asyncio.timeout(20.0):
            try:
                response.models = await _vendor_models(provider, service)
            except httpx.HTTPStatusError as exc:
                response.unsupported = exc.response.status_code in {404, 405, 501}
                response.warnings.append(f"模型列表接口返回 HTTP {exc.response.status_code}，可手动填写模型 ID")
            except (httpx.HTTPError, ValueError):
                response.warnings.append("供应商模型列表获取失败，可手动填写模型 ID")
            if model_name is not None:
                response.models = [
                    next(
                        (item for item in response.models if item.model_name == model_name),
                        ModelMetadata(model_name=model_name),
                    ),
                ]
            if provider.provider_type == "local" and model_name is not None and service == ServiceType.llm:
                base = configured_base_url(provider.provider_type, provider.base_url, service)
                props_base = _replace_suffix(base, "/v1", "")
                try:
                    props = await _json(
                        props_base,
                        provider.api_key,
                        "props",
                        params={"model": model_name, "autoload": "false"},
                    )
                    window = _positive(_record(props.get("default_generation_settings")).get("n_ctx"))
                    if window is not None:
                        response.models[0].context_window = window
                        response.models[0].field_sources["context_window"] = "本地部署"
                except (httpx.HTTPError, ValueError):
                    pass
            try:
                catalog, response.catalog_updated_at = await _openrouter(refresh)
            except (httpx.HTTPError, ValueError):
                response.warnings.append("OpenRouter 模型信息暂不可用，缺失字段可手动补全")
                catalog = []
            for index, model in enumerate(response.models):
                matches = catalog_matches(catalog_id or model.model_name, catalog)
                if len(matches) == 1:
                    response.models[index] = _enrich(model, matches[0])
                elif matches:
                    response.candidates[model.model_name] = matches
    except TimeoutError:
        response.warnings.append("模型发现超过 20 秒，已取得的结果仍可使用，缺失字段可手填")
    return response
