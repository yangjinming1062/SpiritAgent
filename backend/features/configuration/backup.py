"""模块拥有的备份表、校验与恢复操作。"""

from typing import Any
from uuid import uuid4

from infrastructure.ai.config import AIConfig
from infrastructure.backup import BackupContext, BackupTable
from infrastructure.config import SETTINGS
from infrastructure.web.social_platforms import get_social_platform
from infrastructure.web.social_sources import validate_social_credentials
from pydantic import ValidationError

from .ai_config import validate_ai_config
from .models import UserModelConfig, UserSocialAccount
from .settings.models import UserSetting


def _validated_ai_config(value: Any) -> dict[str, Any]:
    """按运行期读取方式整体校验模型配置；配置含供应商密钥，错误只报告字段位置与类型，不回显取值。"""
    try:
        config = AIConfig.model_validate(value)
        validate_ai_config(config, SETTINGS.ai_config)
        return config.model_dump(mode="json")
    except ValidationError as exc:
        fields = ", ".join(
            f"{'.'.join(str(part) for part in error['loc']) or 'ai_config'} ({error['type']})"
            for error in exc.errors(include_input=False, include_url=False)
        )
        raise ValueError(f"Invalid AI config: {fields}") from None


def _prepare_ai_config(_raw: dict[str, Any], payload: dict[str, Any], _context: BackupContext) -> None:
    payload["ai_config"] = _validated_ai_config(payload.get("ai_config"))


def _prepare_social_account(_raw: dict[str, Any], payload: dict[str, Any], _context: BackupContext) -> None:
    try:
        get_social_platform(payload["platform"])
        if payload.get("credentials") is not None:
            if payload.get("state") not in {"configured", "login_required"}:
                raise ValueError("Invalid social account state")
            payload["credentials"] = validate_social_credentials(payload["platform"], payload["credentials"])
        elif payload.get("state") != "unconfigured":
            raise ValueError("Missing social credentials")
    except (ValueError, KeyError, TypeError):
        raise ValueError("Invalid social account credentials") from None
    payload["revision"] = str(uuid4())


def backup_tables() -> tuple[BackupTable, ...]:
    return (
        BackupTable(
            "user_model_configs",
            UserModelConfig,
            unique_keys=(),
            prepare_payload=_prepare_ai_config,
        ),
        BackupTable(
            "user_social_accounts",
            UserSocialAccount,
            unique_keys=("platform",),
            excluded_columns=frozenset(
                {
                    "revision",
                    "login_revision",
                    "login_session_id",
                    "login_state",
                    "login_expires_at",
                    "login_verification_error",
                }
            ),
            prepare_payload=_prepare_social_account,
        ),
        BackupTable(
            "user_settings",
            UserSetting,
            unique_keys=("setting_key",),
        ),
    )
