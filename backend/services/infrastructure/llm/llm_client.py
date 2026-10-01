import asyncio
import time
from collections.abc import Iterable
from dataclasses import replace
from functools import partial

from components import SETTINGS, AIConfig, ProviderCard, get_logger
from modules.auth import UserModelConfig
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .llm_debug import log_event, new_call_id, truncate_for_log
from .providers import (
    BaseProvider,
    ChatProvider,
    EmbeddingProvider,
    ProviderConfig,
    ServiceType,
    default_base_url,
    default_model_for,
    provider_requires_api_key,
    providers_supporting,
    resolve,
    try_resolve_chat,
)

logger = get_logger(__name__)


def scale_temperature(provider_name: str | None, normalized: float) -> float:
    """把 [0, 1] 归一化温度按供应商换算为其原生刻度；未知名或未传供应商时回退到基类默认区间。"""
    cls = try_resolve_chat(provider_name) if provider_name else None
    return (cls or ChatProvider).scale_temperature(normalized)


class MissingLlmConfigError(Exception):
    """能力链为空时抛出；调用方按端点协议映射为 400 响应包。"""


async def _load_user_config(
    db: AsyncSession | None,
    user_id: int | None,
) -> UserModelConfig | None:
    if db is None or user_id is None:
        return None
    return (
        await db.execute(
            select(UserModelConfig).where(UserModelConfig.user_id == user_id),
        )
    ).scalar_one_or_none()


def _chain_entry(
    service_type: ServiceType,
    provider: str,
    *,
    api_key: str,
    base_url: str,
    model: str,
) -> ProviderConfig | None:
    """补齐供应商默认端点与模型；缺少必需密钥、端点或未注册该能力时返回 None。"""
    resolved_model = model or default_model_for(provider, service_type)
    if service_type == ServiceType.llm and not resolved_model:
        return None
    base_url = base_url or default_base_url(provider, service_type)
    # 同一 MiniMax 卡片的端点含 /v1 供 chat SDK 使用；其余能力的请求路径自带版本前缀。
    if provider == "minimax" and service_type != ServiceType.llm:
        base_url = base_url.removesuffix("/v1")
    if not base_url or provider not in providers_supporting(service_type):
        return None
    if not api_key and provider_requires_api_key(service_type, provider):
        return None
    return ProviderConfig(
        base_url=base_url,
        api_key=api_key,
        model=resolved_model,
        service_type=service_type,
        provider_name=provider,
        model_overridden=bool(model),
    )


def _chain_from_ai_config(
    config: AIConfig,
    service_type: ServiceType,
    inherited_sources: Iterable[ProviderCard] = (),
) -> list[ProviderConfig]:
    """密钥与端点按 卡片 → 本配置信息库 → 继承信息库 取值；模型只看能力卡片，空则用供应商默认。"""
    inherited = {card.provider: card for card in inherited_sources}
    sources = {card.provider: card for card in config.providers}
    result: list[ProviderConfig] = []
    for card in getattr(config.capabilities, service_type):
        layers = [
            layer for layer in (card, sources.get(card.provider), inherited.get(card.provider)) if layer is not None
        ]
        entry = _chain_entry(
            service_type,
            card.provider,
            api_key=next((layer.api_key for layer in layers if layer.api_key), ""),
            base_url=next((layer.base_url for layer in layers if layer.base_url), ""),
            model=card.model_name,
        )
        if entry is not None:
            result.append(entry)
    return result


async def resolve_provider_chain(
    db: AsyncSession | None,
    user_id: int | None,
    service_type: str,
) -> list[ProviderConfig]:
    service = ServiceType(service_type)
    user_cfg = await _load_user_config(db, user_id)
    if user_cfg is not None:
        user_ai_config = AIConfig.model_validate(user_cfg.ai_config)
        if getattr(user_ai_config.capabilities, service):
            return _chain_from_ai_config(user_ai_config, service, SETTINGS.ai_config.providers)
    return _chain_from_ai_config(SETTINGS.ai_config, service)


async def resolve_provider_config(
    db: AsyncSession | None,
    user_id: int | None,
    service_type: str,
) -> ProviderConfig:
    """返回能力链首项；链为空时抛出 ``MissingLlmConfigError``。"""
    chain = await resolve_provider_chain(db, user_id, service_type)
    if not chain:
        raise MissingLlmConfigError(
            f"no provider configured for service {service_type!r}",
        )
    return chain[0]


async def resolve_vision_chain(db: AsyncSession | None, user_id: int | None) -> list[ProviderConfig]:
    """筛选视觉供应商；仅默认模型自动选择视觉变体，保留显式模型配置。"""
    chain: list[ProviderConfig] = []
    for cfg in await resolve_provider_chain(db, user_id, ServiceType.llm):
        cls = try_resolve_chat(cfg.provider_name)
        if cls is not None and cls.supports_vision:
            vision_model = "" if cfg.model_overridden else cls.DEFAULT_VISION_MODEL
            chain.append(replace(cfg, model=vision_model) if vision_model else cfg)
    return chain


async def resolve_video_chain(db: AsyncSession | None, user_id: int | None) -> list[ProviderConfig]:
    """筛选接受 input_video 的 chat 供应商。"""
    return [
        cfg
        for cfg in await resolve_provider_chain(db, user_id, ServiceType.llm)
        if (cls := try_resolve_chat(cfg.provider_name)) is not None and cls.supports_video
    ]


def provider_from_config(config: ProviderConfig) -> BaseProvider:
    cls = resolve(config.service_type, config.provider_name)
    return cls(config)


def build_provider[P: BaseProvider](config: ProviderConfig, provider_type: type[P]) -> P:
    """构造供应商并核对其能力类型；注册表与能力类型不符时明确失败。"""
    provider = provider_from_config(config)
    if not isinstance(provider, provider_type):
        raise LookupError(f"provider {config.provider_name!r} is not a {provider_type.__name__}")
    return provider


async def resolve_embedding_provider(
    db: AsyncSession,
    user_id: int,
) -> EmbeddingProvider | None:
    """能力链首个 embedding 供应商；未配置或解析失败时返回 None，记忆降级为关键词召回。"""
    try:
        chain = await resolve_provider_chain(db, user_id, ServiceType.embedding)
        return build_provider(chain[0], EmbeddingProvider) if chain else None
    except Exception:
        logger.warning(
            "embedding provider resolution failed; falling back to keyword-only memory",
            extra={"user_id": user_id},
            exc_info=True,
        )
        return None


async def _embed(
    texts: list[str],
    provider: EmbeddingProvider | None,
    *,
    user_id: int | None,
    timeout_seconds: float,
    purpose: str,
) -> list[list[float]] | None:
    """调用 embedding 并记调试日志；未配置或失败时返回 None。"""
    started = time.monotonic()
    log = partial(
        log_event,
        call_id=new_call_id(),
        service="embedding",
        provider=provider.provider_name if provider else "(none)",
        model=provider.config.model if provider else "(none)",
        call_site=__name__,
        user_id=user_id,
    )
    log(phase="request", text_preview=truncate_for_log(texts[0])[0], num_texts=len(texts))
    if provider is None:
        log(phase="response", status="skipped", reason="no_provider", latency_ms=0)
        return None
    try:
        vectors = await asyncio.wait_for(provider.embed(texts, purpose=purpose), timeout=timeout_seconds)
    except Exception as exc:
        # 记忆随即降级为关键词召回；只记供应商与错误类别，不记输入文本
        logger.warning(
            "embedding failed; memory falls back to keyword recall",
            extra={
                "provider": provider.provider_name,
                "model": provider.config.model,
                "error_type": type(exc).__name__,
                "status_code": getattr(exc, "status_code", None),
            },
        )
        log(
            phase="response",
            status="error",
            latency_ms=int((time.monotonic() - started) * 1000),
            error={"type": type(exc).__name__, "message": str(exc)[:500]},
        )
        return None
    log(
        phase="response",
        status="success",
        latency_ms=int((time.monotonic() - started) * 1000),
        vector_dim=len(vectors[0]) if vectors else 0,
        num_vectors=len(vectors),
    )
    return vectors


async def generate_embedding(
    text: str,
    provider: EmbeddingProvider | None,
    *,
    user_id: int | None = None,
    timeout_seconds: float = 2.0,
    purpose: str = "db",
) -> list[float] | None:
    """为单段文本生成 embedding 向量；未配置或失败时返回 None。purpose 见 EmbeddingProvider.embed。"""
    if not text or not text.strip():
        return None
    vectors = await _embed([text], provider, user_id=user_id, timeout_seconds=timeout_seconds, purpose=purpose)
    return vectors[0] if vectors else None


async def generate_embeddings(
    texts: list[str],
    provider: EmbeddingProvider | None,
    *,
    user_id: int | None = None,
    timeout_seconds: float = 5.0,
) -> list[list[float]] | None:
    """为多段文本生成 embedding 向量列表。"""
    if not texts:
        return []
    return await _embed(texts, provider, user_id=user_id, timeout_seconds=timeout_seconds, purpose="db")
