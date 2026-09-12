import asyncio
import time
from collections.abc import Iterable
from dataclasses import replace
from typing import Any

from components import SETTINGS, AIConfig, ProviderCard, get_logger, load_ai_config
from modules.auth import UserModelConfig
from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .llm_debug import log_event, new_call_id, truncate_for_log
from .providers import (
    OPENAI_COMPATIBLE_PROVIDERS,
    BaseProvider,
    EmbeddingProvider,
    ProviderConfig,
    ServiceType,
    default_base_url,
    default_model_for,
    default_video_model_for,
    default_vision_model_for,
    providers_supporting,
    resolve,
    supports_video,
    supports_vision,
    try_resolve,
)
from .providers.http import get_async_client
from .providers.openai_responses import OpenAIResponsesChatProvider

logger = get_logger(__name__)


def _log_embedding(
    *,
    call_id: str,
    phase: str,
    provider: str,
    model: str,
    user_id: int | None,
    status: str | None = None,
    latency_ms: int | None = None,
    **extras: Any,
) -> None:
    """Embedding 入口拥有稳定的调用方默认字段（service / call_site），在此处统一注入，使调用方只需关心每次事件的字段。"""
    log_event(
        call_id=call_id,
        service="embedding",
        provider=provider,
        model=model,
        call_site=__name__,
        phase=phase,
        status=status,
        latency_ms=latency_ms,
        user_id=user_id,
        **extras,
    )


def client_for_config(llm_config: dict) -> AsyncOpenAI:
    """从已解析的 ``llm_config`` 字典构建 ``AsyncOpenAI``；缺键时抛 ``KeyError``（可能拿到不完整字典的调用方如后台队列需自行预校验）。"""
    return get_async_client(llm_config["api_key"], llm_config["base_url"])


def scale_temperature(provider_name: str | None, normalized: float) -> float:
    """把 [0, 1] 归一化温度按供应商换算为其原生刻度；未知名或未传供应商时回退到基类默认区间。"""
    if provider_name:
        cls = try_resolve(ServiceType.llm, provider_name)
        if cls is not None and hasattr(cls, "scale_temperature"):
            return cls.scale_temperature(normalized)
    return OpenAIResponsesChatProvider.scale_temperature(normalized)


class MissingLlmConfigError(Exception):
    """用户级 LLM 配置缺失时抛出；调用方按端点协议映射为 400 响应包。"""


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


def _chain_from_ai_config(
    config: AIConfig,
    service_type: str,
    inherited_sources: Iterable[ProviderCard] = (),
) -> list[ProviderConfig]:
    inherited = {card.provider: card for card in inherited_sources}
    sources = {card.provider: card for card in config.providers}
    cards = getattr(config.capabilities, service_type)
    supporting = set(providers_supporting(service_type))
    result: list[ProviderConfig] = []
    for card in cards:
        source = sources.get(card.provider)
        fallback = inherited.get(card.provider)
        api_key = card.api_key or (source.api_key if source else "") or (fallback.api_key if fallback else "")
        base_url = (
            card.base_url
            or (source.base_url if source else "")
            or (fallback.base_url if fallback else "")
            or default_base_url(card.provider, service_type)
        )
        model = (
            card.model_name
            or (source.model_name if source else "")
            or (fallback.model_name if fallback else "")
            or default_model_for(card.provider, service_type)
        )
        if card.provider == "minimax" and service_type != "llm" and base_url.endswith("/v1"):
            base_url = base_url[:-3]
        if api_key and base_url and card.provider in supporting:
            result.append(
                ProviderConfig(
                    base_url=base_url,
                    api_key=api_key,
                    model=model,
                    service_type=ServiceType(service_type),
                    provider_name=card.provider,
                    model_overridden=bool(
                        card.model_name or (source and source.model_name) or (fallback and fallback.model_name),
                    ),
                ),
            )
    return result


def _embedding_chain_from_ai_config(
    config: AIConfig,
    inherited_sources: Iterable[ProviderCard] = (),
) -> list[ProviderConfig]:
    inherited = {card.provider: card for card in inherited_sources}
    supporting = set(providers_supporting(ServiceType.embedding))
    result: list[ProviderConfig] = []
    for card in config.providers:
        fallback = inherited.get(card.provider)
        api_key = card.api_key or (fallback.api_key if fallback else "")
        base_url = (
            card.base_url
            or (fallback.base_url if fallback else "")
            or default_base_url(card.provider, ServiceType.embedding)
        )
        if card.provider == "minimax" and base_url.endswith("/v1"):
            base_url = base_url[:-3]
        if api_key and base_url and card.provider in supporting:
            result.append(
                ProviderConfig(
                    base_url=base_url,
                    api_key=api_key,
                    model=default_model_for(card.provider, ServiceType.embedding),
                    service_type=ServiceType.embedding,
                    provider_name=card.provider,
                ),
            )
    return result


async def resolve_provider_chain(
    db: AsyncSession | None,
    user_id: int | None,
    service_type: str,
    *,
    user_cfg: UserModelConfig | None = None,
) -> list[ProviderConfig]:
    if user_cfg is None:
        user_cfg = await _load_user_config(db, user_id)
    if service_type == ServiceType.embedding:
        system_chain = _embedding_chain_from_ai_config(SETTINGS.ai_config)
        if user_cfg is None:
            return system_chain
        user_chain = _embedding_chain_from_ai_config(
            load_ai_config(user_cfg.ai_config),
            SETTINGS.ai_config.providers,
        )
        seen = {config.provider_name for config in user_chain}
        return user_chain + [config for config in system_chain if config.provider_name not in seen]
    if user_cfg is not None:
        user_ai_config = load_ai_config(user_cfg.ai_config)
        user_cards = getattr(user_ai_config.capabilities, service_type)
        if user_cards:
            return _chain_from_ai_config(
                user_ai_config,
                service_type,
                SETTINGS.ai_config.providers,
            )
    return _chain_from_ai_config(SETTINGS.ai_config, service_type)


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


async def resolve_vision_chain(
    db: AsyncSession | None,
    user_id: int | None,
    *,
    service_type: str = "llm",
) -> list[ProviderConfig]:
    """筛选视觉供应商；仅默认模型自动选择视觉变体，保留显式模型配置。"""
    return [
        replace(
            cfg,
            model=cfg.model if cfg.model_overridden else default_vision_model_for(cfg.provider_name) or cfg.model,
        )
        for cfg in await resolve_provider_chain(db, user_id, service_type)
        if supports_vision(cfg.provider_name)
    ]


async def resolve_video_chain(
    db: AsyncSession | None,
    user_id: int | None,
    *,
    service_type: str = "llm",
) -> list[ProviderConfig]:
    """筛选视频理解供应商；仅默认模型自动选择视频变体，保留显式模型配置。"""
    return [
        replace(
            cfg,
            model=cfg.model if cfg.model_overridden else default_video_model_for(cfg.provider_name) or cfg.model,
        )
        for cfg in await resolve_provider_chain(db, user_id, service_type)
        if supports_video(cfg.provider_name)
    ]


def provider_from_config(config: ProviderConfig) -> BaseProvider:
    """从已解析的 config 直接构造供应商实例，跳过 ``provider_for_service`` 的 DB 查询。"""
    cls = resolve(config.service_type, config.provider_name)
    return cls(config)


async def provider_for_service(
    db: AsyncSession | None,
    user_id: int | None,
    service_type: str,
) -> BaseProvider:
    """解析 config 并实例化供应商，返回链首；多供应商回退请用 ``execute_with_fallback``。"""
    return provider_from_config(
        await resolve_provider_config(db, user_id, service_type),
    )


async def resolve_embedding_provider(
    db: AsyncSession,
    user_id: int,
) -> EmbeddingProvider | None:
    try:
        chain = await resolve_provider_chain(db, user_id, "embedding")
        if not chain:
            # 回退到 chat 供应商并使用 OpenAI 兼容的默认 embedding 模型，但仅限真正暴露 OpenAI 形态 ``/v1/embeddings`` 端点的供应商；原生供应商（minimax 用 ``texts`` 而非 ``input``）会 404 / 返回畸形 body —— 会静默降级语义记忆而不暴露误配。
            llm_cfg = await resolve_provider_config(db, user_id, "llm")
            if llm_cfg.provider_name not in OPENAI_COMPATIBLE_PROVIDERS:
                return None
            chain = [
                ProviderConfig(
                    base_url=llm_cfg.base_url,
                    api_key=llm_cfg.api_key,
                    model="text-embedding-3-small",
                    service_type=ServiceType.embedding,
                    provider_name=llm_cfg.provider_name,
                ),
            ]
        provider = provider_from_config(chain[0])
        return provider if isinstance(provider, EmbeddingProvider) else None
    except (MissingLlmConfigError, LookupError):
        # 配置缺失/无匹配供应商是预期的「关停语义记忆」路径；其它异常继续记录再降级，避免误配完全隐形。
        return None
    except Exception:
        logger.warning(
            "embedding provider resolution failed; falling back to keyword-only memory",
            extra={"user_id": user_id},
            exc_info=True,
        )
        return None


async def generate_embedding(
    text: str,
    provider: EmbeddingProvider | None,
    *,
    user_id: int | None = None,
    timeout_seconds: float = 2.0,
) -> list[float] | None:
    """为单段文本生成 embedding 向量；未配置或失败时返回 None。"""
    if not text or not text.strip():
        return None
    call_id = new_call_id()
    _log_embedding(
        call_id=call_id,
        phase="request",
        provider="(resolving)",
        model="(resolving)",
        user_id=user_id,
        text_preview=truncate_for_log(text)[0],
        num_texts=1,
    )
    started = time.monotonic()
    try:
        if provider is None:
            _log_embedding(
                call_id=call_id,
                phase="response",
                provider="(none)",
                model="(none)",
                user_id=user_id,
                status="skipped",
                reason="no_provider",
                latency_ms=int((time.monotonic() - started) * 1000),
            )
            return None
        _log_embedding(
            call_id=call_id,
            phase="provider_resolved",
            provider=provider.provider_name,
            model=getattr(provider.config, "model", ""),
            user_id=user_id,
        )
        result = await asyncio.wait_for(
            provider.embed_one(text),
            timeout=timeout_seconds,
        )
        _log_embedding(
            call_id=call_id,
            phase="response",
            provider=provider.provider_name,
            model=getattr(provider.config, "model", ""),
            user_id=user_id,
            status="success",
            latency_ms=int((time.monotonic() - started) * 1000),
            vector_dim=len(result) if result else 0,
        )
        return result
    except Exception as exc:
        logger.debug("generate_embedding failed", extra={"error": str(exc)})
        _log_embedding(
            call_id=call_id,
            phase="response",
            provider="(unknown)",
            model="(unknown)",
            user_id=user_id,
            status="error",
            latency_ms=int((time.monotonic() - started) * 1000),
            error={"type": type(exc).__name__, "message": str(exc)[:500]},
        )
        return None


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
    call_id = new_call_id()
    _log_embedding(
        call_id=call_id,
        phase="request",
        provider="(resolving)",
        model="(resolving)",
        user_id=user_id,
        text_preview=truncate_for_log(texts[0])[0],
        num_texts=len(texts),
    )
    started = time.monotonic()
    try:
        if provider is None:
            _log_embedding(
                call_id=call_id,
                phase="response",
                provider="(none)",
                model="(none)",
                user_id=user_id,
                status="skipped",
                reason="no_provider",
                latency_ms=int((time.monotonic() - started) * 1000),
            )
            return None
        _log_embedding(
            call_id=call_id,
            phase="provider_resolved",
            provider=provider.provider_name,
            model=getattr(provider.config, "model", ""),
            user_id=user_id,
        )
        result = await asyncio.wait_for(provider.embed(texts), timeout=timeout_seconds)
        _log_embedding(
            call_id=call_id,
            phase="response",
            provider=provider.provider_name,
            model=getattr(provider.config, "model", ""),
            user_id=user_id,
            status="success",
            latency_ms=int((time.monotonic() - started) * 1000),
            vector_dim=len(result[0]) if result else 0,
            num_vectors=len(result) if result else 0,
        )
        return result
    except Exception as exc:
        logger.debug("generate_embeddings failed", extra={"error": str(exc)})
        _log_embedding(
            call_id=call_id,
            phase="response",
            provider="(unknown)",
            model="(unknown)",
            user_id=user_id,
            status="error",
            latency_ms=int((time.monotonic() - started) * 1000),
            error={"type": type(exc).__name__, "message": str(exc)[:500]},
        )
        return None
