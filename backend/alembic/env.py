import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, make_url, pool

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import modules
import modules.media.models  # noqa: F401 — video_gen_jobs is intentionally not imported by modules/__init__
from common.model import ModelBase
from components import SETTINGS

config = context.config
# fileConfig 默认会替换 root logger；main.py 内启动迁移时（lifespan 已先 setup_logging）必须跳过，否则 web 进程会"失明"。仅 CLI 调用 alembic 时才配置日志。
if config.config_file_name and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# URL 优先级：调用方显式注入（main.py 启动迁移）> DATABASE_URL 环境变量 > SETTINGS。
if not config.get_main_option("sqlalchemy.url"):
    url = make_url(os.environ.get("DATABASE_URL") or SETTINGS.database_url)
    url = url.set(drivername="postgresql+psycopg").render_as_string(hide_password=False)
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))

target_metadata = ModelBase.metadata


def _include_object(obj, name, type_, reflected, compare_to):
    # PG 特有的索引（partial unique / hnsw / gin trgm）只在迁移里声明——ModelBase.metadata 无法表达 partial WHERE / vector / trgm ops。autogenerate 默认会把"仅存在数据库"的对象视为"模型少了"，提议删除迁移里手工建的索引；这里跳过让 autogenerate 信任数据库现状。
    return not (type_ == "index" and reflected and compare_to is None)


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_object=_include_object,
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
            include_object=_include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
