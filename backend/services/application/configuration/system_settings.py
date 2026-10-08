import asyncio
import json
from typing import Any

from components import (
    CAPABILITY_SERVICES,
    SETTINGS,
    AIConfigUpdate,
    get_logger,
    setup_logging,
)
from modules.settings import SystemSetting
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.domains.configuration import prepare_ai_config, public_ai_config
from services.infrastructure.llm import provider_context_defaults, providers_supporting, rotate_http_clients

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
        "app_name",
    },
)

# 敏感字段：返回给前端时脱敏掩码，保存时空字符串默认保持原值
SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "brave_search_api_key",
        "tavily_api_key",
    },
)


def _validation_fields(exc: ValidationError) -> str:
    return ", ".join(sorted({".".join(str(part) for part in error["loc"]) for error in exc.errors()}))


async def load_and_apply_system_settings(db: AsyncSession) -> None:
    """服务启动时调用：从 system_settings 表中批量加载已保存的动态配置并水合进内存单例 SETTINGS。"""
    async with _SETTINGS_UPDATE_LOCK:
        rows = (await db.execute(select(SystemSetting))).scalars().all()
        try:
            overrides = {
                row.setting_key: json.loads(row.setting_value)
                for row in rows
                if row.setting_key not in STARTUP_ONLY_KEYS and row.setting_key in type(SETTINGS).model_fields
            }
            candidate = SETTINGS.validate_runtime_update(overrides)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid persisted system settings: {exc}") from None
        except ValidationError as exc:
            raise RuntimeError(f"Invalid persisted system settings: {_validation_fields(exc)}") from None

        SETTINGS.apply_runtime_candidate(candidate)
        if overrides:
            logger.info("Loaded %d dynamic system settings from database", len(overrides))
            _apply_runtime_side_effects(set(overrides))


def _apply_runtime_side_effects(changed_keys: set[str]) -> None:
    """日志配置或 LLM 超时/重试参数变更时触发即时副作用。"""
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

    # DynamicLimiter.enabled 直读 SETTINGS.rate_limit_enabled，无需进程内赋值；LLM 超时/重试参数变动时换代连接池让新连接生效。
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


def get_system_settings_for_admin() -> dict[str, Any]:
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
    result["ai_context_defaults"] = provider_context_defaults()
    return result


async def save_system_settings(db: AsyncSession, updates: dict[str, Any]) -> None:
    """合并候选值 → 整批校验 → 事务落库 → 原位更新 SETTINGS → 副作用；校验失败抛 ValueError，不修改运行时。"""
    async with _SETTINGS_UPDATE_LOCK:
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
            if "ai_config" in pending:
                pending["ai_config"] = prepare_ai_config(
                    AIConfigUpdate.model_validate(pending["ai_config"]),
                    SETTINGS.ai_config,
                )
            candidate = SETTINGS.validate_runtime_update(pending)
        except ValidationError as exc:
            raise ValueError(f"系统设置无效，请检查：{_validation_fields(exc)}") from None

        serialized = {
            key: json.dumps(value, ensure_ascii=False)
            for key, value in candidate.model_dump(mode="json", include=set(pending)).items()
        }
        changed_keys = {key for key in pending if getattr(SETTINGS, key) != getattr(candidate, key)}

        try:
            rows = {
                row.setting_key: row
                for row in (
                    await db.execute(select(SystemSetting).where(SystemSetting.setting_key.in_(serialized)))
                ).scalars()
            }
            new_rows: list[SystemSetting] = []
            for key, value in serialized.items():
                if key in rows:
                    rows[key].setting_value = value
                else:
                    new_rows.append(SystemSetting(setting_key=key, setting_value=value))
            if new_rows:
                max_id = await db.scalar(select(func.max(SystemSetting.id)))
                if max_id is not None:
                    # 外部导入（如 CSV 带显式 id 写入）不会推进自增序列，先对齐再插入，避免 nextval 撞已有主键。
                    await db.execute(
                        select(
                            func.setval(
                                func.pg_get_serial_sequence("system_settings", "id"),
                                max_id,
                            ),
                        ),
                    )
                db.add_all(new_rows)
            await db.commit()
        except Exception:
            await db.rollback()
            raise

        SETTINGS.apply_runtime_candidate(candidate)
        if changed_keys:
            logger.info("System settings updated: %s", ", ".join(sorted(changed_keys)))
            _apply_runtime_side_effects(changed_keys)
