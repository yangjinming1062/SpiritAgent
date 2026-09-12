import asyncio
import json
from typing import Any

from components import (
    CAPABILITY_SERVICES,
    SETTINGS,
    AIConfig,
    get_logger,
    setup_logging,
)
from fastapi import HTTPException
from modules.settings import SystemSetting
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.domains.configuration.ai_config import prepare_ai_config, public_ai_config
from services.infrastructure.llm import providers_supporting, rotate_http_clients

logger = get_logger(__name__)
_SETTINGS_UPDATE_LOCK = asyncio.Lock()

# 启动专用核心参数（不可由后台动态变更，必须保持在 config.toml / 环境变量 中）
STARTUP_ONLY_KEYS: frozenset[str] = frozenset(
    {
        "database_url",
        "jwt_secret_key",
        "jwt_algorithm",
        "admin_username",
        "admin_password",
        "companion_asset_signing_key",
        "data_dir",
        "rate_limit_storage_url",
        "metrics_enabled",
        "metrics_path",
        "app_name",
        "api_prefix",
    },
)

# 敏感字段：返回给前端时脱敏掩码，保存时空字符串默认保持原值
SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "tripo_api_key",
        "hunyuan_api_key",
        "seethrough_fallback_token",
        "brave_search_api_key",
        "tavily_api_key",
        "metrics_auth_token",
    },
)


def _parse_setting_value(raw: str) -> Any:
    """尝试以 JSON 解析存储的配置值，失败则作为字符串原样返回。"""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw


def _serialize_setting_value(val: Any) -> str:
    """将配置值序列化为 JSON 字符串存储。"""
    return json.dumps(val, ensure_ascii=False)


def _validation_fields(exc: ValidationError) -> str:
    return ", ".join(sorted({".".join(str(part) for part in error["loc"]) for error in exc.errors()}))


async def load_and_apply_system_settings(db: AsyncSession) -> dict[str, Any]:
    """服务启动时调用：从 system_settings 表中批量加载已保存的动态配置并水合进内存单例 SETTINGS。"""
    async with _SETTINGS_UPDATE_LOCK:
        rows = (await db.execute(select(SystemSetting))).scalars().all()
        overrides = {
            row.setting_key: _parse_setting_value(row.setting_value)
            for row in rows
            if row.setting_key not in STARTUP_ONLY_KEYS and row.setting_key in type(SETTINGS).model_fields
        }
        try:
            candidate = SETTINGS.validate_runtime_update(overrides)
        except ValidationError as exc:
            raise RuntimeError(f"Invalid persisted system settings: {_validation_fields(exc)}") from None

        applied = {key: getattr(candidate, key) for key in overrides}
        SETTINGS.apply_runtime_candidate(candidate)
        if applied:
            logger.info("Loaded %d dynamic system settings from database", len(applied))
            _apply_runtime_side_effects(set(applied))
        return applied


def _apply_runtime_side_effects(changed_keys: set[str]) -> None:
    """当某些特殊配置（日志等级、限流开关等）发生变更时触发即时副作用。"""
    if "log_level" in changed_keys or "log_format" in changed_keys:
        try:
            setup_logging()
            logger.info(
                "Logging configuration reloaded: level=%s format=%s",
                SETTINGS.log_level,
                SETTINGS.log_format,
            )
        except Exception:
            logger.warning("Failed to reload logging configuration", exc_info=True)

    # DynamicLimiter.enabled 的 getter 直读 SETTINGS.rate_limit_enabled，无需进程内赋值。

    # 如果 LLM 请求超时或重试参数发生变动，换代客户端连接池，让新连接应用新参数
    llm_http_keys = {
        "llm_request_timeout_seconds",
        "llm_max_retry_attempts",
        "llm_base_retry_delay",
        "llm_max_retry_delay",
    }
    if changed_keys & llm_http_keys:
        try:
            rotate_http_clients()
            logger.info(
                "LLM HTTP client pools rotated for new timeout/retry settings",
            )
        except Exception:
            logger.warning("Failed to rotate LLM HTTP client caches", exc_info=True)


async def get_system_settings_for_admin(
    _db: AsyncSession | None = None,
) -> dict[str, Any]:
    """查询当前系统全部动态设置供管理后台编辑。敏感 key 不暴露原值，通过 key_set 标识是否已配置。"""
    result: dict[str, Any] = {}
    for key in type(SETTINGS).model_fields:
        if key in STARTUP_ONLY_KEYS:
            continue
        val = getattr(SETTINGS, key)
        if key == "ai_config":
            result[key] = public_ai_config(val).model_dump()
        elif key in SENSITIVE_KEYS:
            result[key] = ""
            result[f"{key}_set"] = bool(val)
        else:
            result[key] = val

    result["ai_provider_support"] = {service: providers_supporting(service) for service in CAPABILITY_SERVICES}
    return result


async def save_system_settings(
    db: AsyncSession,
    updates: dict[str, Any],
) -> dict[str, Any]:
    """保存管理员提交的动态配置，落库并立即热更新内存 SETTINGS。"""
    async with _SETTINGS_UPDATE_LOCK:
        updates = dict(updates)
        if "ai_config" in updates:
            updates["ai_config"] = prepare_ai_config(
                updates["ai_config"],
                SETTINGS.ai_config,
            )
        pending: dict[str, Any] = {}
        for key, val in updates.items():
            if key in STARTUP_ONLY_KEYS or key not in type(SETTINGS).model_fields:
                continue
            if key in SENSITIVE_KEYS:
                if bool(updates.get(f"clear_{key}")):
                    val = ""
                elif val is None or val == "":
                    continue
            pending[key] = val

        try:
            candidate = SETTINGS.validate_runtime_update(pending)
        except ValidationError as exc:
            raise HTTPException(422, f"系统设置无效，请检查：{_validation_fields(exc)}") from None

        normalized = {key: getattr(candidate, key) for key in pending}
        serialized = {
            key: _serialize_setting_value(value.model_dump() if isinstance(value, AIConfig) else value)
            for key, value in normalized.items()
        }
        changed_keys = {key for key, value in normalized.items() if getattr(SETTINGS, key) != value}

        try:
            for key, value in serialized.items():
                row = (
                    await db.execute(select(SystemSetting).where(SystemSetting.setting_key == key))
                ).scalar_one_or_none()
                if row is not None:
                    row.setting_value = value
                else:
                    db.add(SystemSetting(setting_key=key, setting_value=value))
            await db.commit()
        except Exception:
            await db.rollback()
            raise

        SETTINGS.apply_runtime_candidate(candidate)
        if changed_keys:
            logger.info("System settings updated: %s", ", ".join(sorted(changed_keys)))
            _apply_runtime_side_effects(changed_keys)
        return await get_system_settings_for_admin(db)
