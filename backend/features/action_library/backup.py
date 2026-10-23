"""动作素材的备份贡献：定义成组恢复，评审冻结依据随行保存。"""

from infrastructure.backup import BackupTable

from .models import ActionDefinition


def backup_tables() -> tuple[BackupTable, ...]:
    return (
        BackupTable(
            name="action_definitions",
            _model=ActionDefinition,
            foreign_keys={},
            unique_keys=None,
            strict_references=False,
        ),
    )
