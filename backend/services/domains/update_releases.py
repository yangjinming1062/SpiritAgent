"""更新目录恢复；发布权威来自数据库，孤儿产物不自动启用。"""

import asyncio
import re
import shutil
from pathlib import Path
from uuid import uuid4

from components import get_logger, session_scope
from modules.update import UpdateVersion
from sqlalchemy import select

logger = get_logger(__name__)

# 与容器 /app/updates 挂载一致，API 与启动恢复共用。
VERSIONS_DIR = Path("updates/versions")
_VERSION_PATTERN = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")


def _recover_update_directories(registered_versions: frozenset[str]) -> None:
    """仅在单 web 进程接受请求前执行，暂存目录此时不可能有在途上传。"""
    if not VERSIONS_DIR.exists():
        return
    for directory in sorted(VERSIONS_DIR.iterdir()):
        if directory.is_symlink() or not directory.is_dir():
            continue
        try:
            if directory.name.startswith(".upload_"):
                shutil.rmtree(directory)
                logger.info("removed interrupted update upload staging directory")
            elif _VERSION_PATTERN.fullmatch(directory.name) and directory.name not in registered_versions:
                quarantine = VERSIONS_DIR / ".quarantine"
                quarantine.mkdir(exist_ok=True)
                directory.rename(quarantine / f"{directory.name}-{uuid4().hex}")
                logger.warning(
                    "update release has no database record; quarantined, upload again to publish",
                    extra={"version": directory.name},
                )
        except OSError:
            logger.warning("update directory recovery failed", extra={"directory": directory.name}, exc_info=True)
    for version in sorted(registered_versions):
        if _VERSION_PATTERN.fullmatch(version) and not (VERSIONS_DIR / version).is_dir():
            logger.warning("registered update release directory is missing", extra={"version": version})


async def recover_update_storage() -> None:
    """完整读取库存版本后再回收，提交结果未知的已登记版本不被误删。"""
    async with session_scope() as db:
        versions = frozenset(await db.scalars(select(UpdateVersion.version)))
    await asyncio.to_thread(_recover_update_directories, versions)
