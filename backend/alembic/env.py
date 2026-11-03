"""Alembic 迁移环境（异步模式）。

阶段0：建立迁移管理框架；阶段1任务1.5 补充 13 张表 ORM 后生成首版迁移。
所有 Schema 变更（含阶段8 A/B 实验表、LoRA 训练记录表等）均通过 alembic revision 追踪，
禁止手动改库，保证可回滚、可追溯。
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# 让 alembic 能导入应用模块
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.base import Base  # noqa: E402
from app.db.engine_options import database_connect_args, database_url_for_alembic  # noqa: E402
# 触发所有 ORM 模型注册到 Base.metadata，让 autogenerate 能发现
import app.db.models  # noqa: E402, F401

# alembic.ini 配置
config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 注入数据库 URL（覆盖 alembic.ini 中的空值）。
# 用与应用完全相同的构造逻辑：Supabase 的 TLS / 关闭 prepared statement
# 若只配在应用侧，迁移会连不上或报 `prepared statement does not exist`。
config.set_main_option("sqlalchemy.url", database_url_for_alembic())

# 目标 metadata：所有 ORM 模型自动注册到 Base.metadata
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式：生成 SQL 脚本而不连接数据库。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """在线模式：通过给定连接执行迁移。"""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """在线异步模式：使用 async engine 执行迁移。"""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args=database_connect_args(),
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
