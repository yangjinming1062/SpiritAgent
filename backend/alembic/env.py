import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import modules  # noqa: F401  导入即把全部 ORM 模型注册到 ModelBase.metadata
from common import ModelBase
from components import database_url

config = context.config
# fileConfig 默认会替换 root logger；启动迁移（bootstrap/lifecycle.py::_run_migrations 设置 configure_logger=False，lifespan 已先 setup_logging）必须跳过，否则 web 进程会"失明"。仅 CLI 调用 alembic 时才配置日志。
if config.config_file_name and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# 调用方显式注入优先，否则取 components.database_url（SETTINGS 已按 环境变量 > .env > config.toml 解析）。
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", database_url("postgresql+psycopg").replace("%", "%%"))

# 索引只比对名称、唯一性和列表达式，不比对 WHERE / USING / 操作符类；这些在模型与迁移间须人工保持一致。
target_metadata = ModelBase.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
