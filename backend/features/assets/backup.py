"""备份正式资产清单；文件复制及哈希核对由备份流程负责。"""

from infrastructure.backup import BackupTable
from sqlalchemy import select

from .models import Asset


def backup_tables() -> tuple[BackupTable, ...]:
    return (
        BackupTable(
            "assets",
            Asset,
            unique_keys=("path",),
            export_query=lambda user_id: select(Asset).where(
                Asset.user_id == user_id, Asset.state != "deleted", Asset.content_hash.is_not(None)
            ),
        ),
    )
