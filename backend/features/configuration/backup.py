"""模块拥有的备份表、校验与恢复操作。"""

from functools import partial
from typing import Any

from infrastructure.ai.config import AIConfig
from infrastructure.backup import BackupContext, BackupTable
from infrastructure.config import SETTINGS
from pydantic import ValidationError

from .ai_config import validate_ai_config
from .models import UserModelConfig
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


def _prepare(table: str, raw: dict[str, Any], payload: dict[str, Any], context: BackupContext) -> None:

    if table == "user_model_configs":
        payload["ai_config"] = _validated_ai_config(payload.get("ai_config"))


def backup_tables() -> tuple[BackupTable, ...]:
    return (
        BackupTable(
            "user_model_configs",
            UserModelConfig,
            unique_keys=(),
            prepare_payload=partial(_prepare, "user_model_configs"),
        ),
        BackupTable(
            "user_settings",
            UserSetting,
            unique_keys=("setting_key",),
            prepare_payload=partial(_prepare, "user_settings"),
        ),
    )
