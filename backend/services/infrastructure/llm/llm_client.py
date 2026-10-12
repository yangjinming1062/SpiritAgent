import asyncio
import hashlib
import json
import time
from collections.abc import Sequence
from dataclasses import replace
from functools import partial
from uuid import UUID

from components import (
    AI_FEATURE_BY_ID,
    SETTINGS,
    AIConfig,
    AiFeature,
    CapabilityCard,
    ModelModality,
    ProviderCard,
    get_logger,
)
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
    configured_base_url,
    effective_input_modalities,
    model_supports_feature,
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


class MissingVideoModelError(MissingLlmConfigError):
    """会话含视频附件，但能力链中没有支持视频理解的模型；与链为空区分，以便给出针对性提示。"""


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
    provider: ProviderCard,
    card: CapabilityCard,
) -> ProviderConfig | None:
    """凭据仅从供应商实例取得；未配置必需凭据或端点的模型不可调用。"""
    provider_type = provider.provider_type
    if provider_type == "local" and service_type == ServiceType.image_gen and card.model_name != "qwen":
        raise MissingLlmConfigError("本地生图使用固定 Qwen 工作流")
    base_url = configured_base_url(provider_type, provider.base_url, service_type)
    if not base_url or provider_type not in providers_supporting(service_type):
        return None
    if not provider.api_key and provider_requires_api_key(service_type, provider_type):
        return None
    return ProviderConfig(
        base_url=base_url,
        api_key=provider.api_key if not (provider_type == "local" and service_type == ServiceType.image_gen) else "",
        model=card.model_name,
        service_type=service_type,
        provider_name=provider_type,
        provider_id=provider.id,
        model_card_id=card.id,
        context_window=card.context_window,
        max_output_tokens=card.max_output_tokens,
        max_input_tokens=card.max_input_tokens,
        input_modalities=card.input_modalities,
        output_modalities=card.output_modalities,
    )


def _chain_from_ai_config(
    config: AIConfig,
    service_type: ServiceType,
    system: AIConfig | None = None,
    *,
    card_ids: Sequence[UUID] | None = None,
) -> list[ProviderConfig]:
    models: list[tuple[CapabilityCard, AIConfig]] = []
    if card_ids is None:
        models = [(card, config) for card in getattr(config.capabilities, service_type)]
    else:
        for model_id in card_ids:
            model = config.find_model(model_id, system)
            if model is None or model[0] != service_type:
                raise MissingLlmConfigError(f"功能引用的模型卡片无效：{model_id}")
            models.append((model[1], model[2]))
    # 向量先选模型再核对凭据；首张失效不能改用另一向量空间。
    if service_type == ServiceType.embedding:
        models = models[:1]
    result: list[ProviderConfig] = []
    for card, owner in models:
        provider = owner.find_provider(card.provider_id, system if owner is config else None)
        if provider is None:
            raise MissingLlmConfigError(f"模型引用的供应商不存在：{card.provider_id}")
        entry = _chain_entry(service_type, provider, card)
        if entry is not None:
            result.append(entry)
    return result


async def resolve_provider_chain(
    db: AsyncSession | None,
    user_id: int | None,
    service_type: str,
    *,
    feature: AiFeature | None = None,
) -> list[ProviderConfig]:
    service = ServiceType(service_type)
    if feature is not None and AI_FEATURE_BY_ID[feature].service != service:
        raise ValueError(f"AI feature {feature.value!r} does not use {service.value!r}")
    user_cfg = await _load_user_config(db, user_id)
    user_ai = AIConfig.model_validate(user_cfg.ai_config) if user_cfg is not None else AIConfig()
    system_ai = SETTINGS.ai_config
    user_feature = user_ai.features.get(feature) if feature is not None else None
    system_feature = system_ai.features.get(feature) if feature is not None else None
    if user_feature is not None and user_feature.chain:
        owner, card_ids, source = user_ai, user_feature.chain, "user_feature"
    elif system_feature is not None and system_feature.chain:
        owner, card_ids, source = system_ai, system_feature.chain, "system_feature"
    elif getattr(user_ai.capabilities, service):
        owner, card_ids, source = user_ai, None, "user_default"
    else:
        owner, card_ids, source = system_ai, None, "system_default"
    policies = [value for value in (user_feature, system_feature) if value is not None]
    attempt = next(
        (value.attempt_timeout_seconds for value in policies if value.attempt_timeout_seconds is not None),
        None,
    )
    total = next((value.total_timeout_seconds for value in policies if value.total_timeout_seconds is not None), None)
    chain = [
        replace(
            config,
            feature=feature,
            config_source=source,
            attempt_timeout_seconds=attempt,
            total_timeout_seconds=total,
        )
        for config in _chain_from_ai_config(
            owner,
            service,
            system_ai if owner is user_ai else None,
            card_ids=card_ids,
        )
    ]
    return (
        [
            config
            for config in chain
            if model_supports_feature(
                config.provider_name,
                config.model,
                feature,
                input_modalities=config.input_modalities,
                output_modalities=config.output_modalities,
            )
        ]
        if feature is not None
        else chain
    )


async def resolve_provider_config(
    db: AsyncSession | None,
    user_id: int | None,
    service_type: str,
    *,
    feature: AiFeature | None = None,
) -> ProviderConfig:
    """返回已解析调用链首项；链为空时抛出 ``MissingLlmConfigError``。"""
    chain = await resolve_provider_chain(db, user_id, service_type, feature=feature)
    if not chain:
        raise MissingLlmConfigError(
            f"no provider configured for service {service_type!r}",
        )
    return chain[0]


def _vision_configs(configs: Sequence[ProviderConfig]) -> list[ProviderConfig]:
    """按视觉能力筛选，实际模型始终由能力卡片指定。"""
    return [cfg for cfg in configs if ModelModality.image in effective_input_modalities(cfg)]


async def resolve_vision_chain(
    db: AsyncSession | None,
    user_id: int | None,
    *,
    feature: AiFeature | None = None,
) -> list[ProviderConfig]:
    return _vision_configs(await resolve_provider_chain(db, user_id, ServiceType.llm, feature=feature))


async def resolve_video_chain(
    db: AsyncSession | None,
    user_id: int | None,
    *,
    feature: AiFeature | None = None,
) -> list[ProviderConfig]:
    """筛选接受 input_video 的 chat 供应商。"""
    return [
        cfg
        for cfg in await resolve_provider_chain(db, user_id, ServiceType.llm, feature=feature)
        if ModelModality.video in effective_input_modalities(cfg)
    ]


async def resolve_media_chain(
    db: AsyncSession | None,
    user_id: int | None,
    *,
    feature: AiFeature,
    required_modalities: frozenset[ModelModality],
) -> list[ProviderConfig]:
    return [
        config
        for config in await resolve_provider_chain(db, user_id, ServiceType.llm, feature=feature)
        if required_modalities <= effective_input_modalities(config)
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
    """记忆向量功能的 embedding 供应商；未配置或解析失败时返回 None，降级为关键词召回。"""
    try:
        chain = await resolve_provider_chain(db, user_id, ServiceType.embedding, feature=AiFeature.memory_embedding)
        return build_provider(chain[0], EmbeddingProvider) if chain else None
    except Exception:
        logger.warning(
            "embedding provider resolution failed; falling back to keyword-only memory",
            extra={"user_id": user_id},
            exc_info=True,
        )
        return None


def embedding_model_key(config: ProviderConfig) -> str:
    """端点或模型变化产生新的向量空间；密钥轮换不改变空间。"""
    identity = (config.provider_name, config.base_url.rstrip("/"), config.model)
    return hashlib.sha256(json.dumps(identity, ensure_ascii=True).encode("utf-8")).hexdigest()


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
    log(
        phase="request",
        text_preview=truncate_for_log(texts[0])[0] if SETTINGS.llm_debug_logging else None,
        num_texts=len(texts),
    )
    if provider is None:
        log(phase="response", status="skipped", reason="no_provider", latency_ms=0)
        return None
    try:
        configured = [
            seconds
            for seconds in (provider.config.attempt_timeout_seconds, provider.config.total_timeout_seconds)
            if seconds is not None
        ]
        budget = min(configured) if configured else timeout_seconds
        vectors = await asyncio.wait_for(provider.embed(texts, purpose=purpose), timeout=budget)
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
