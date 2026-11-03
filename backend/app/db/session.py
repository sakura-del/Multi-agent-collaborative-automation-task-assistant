"""异步数据库会话管理。

阶段0：仅建立 engine 与 AsyncSession 工厂，
实际 ORM 模型在阶段1补齐后即可正常使用。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings
from app.core.logging import get_logger
from app.db.engine_options import (
    database_connect_args,
    database_engine_kwargs,
    prepare_database_url,
)

logger = get_logger(__name__)


def _build_engine():
    """根据 dialect 自适应构造 async engine。

    SQLite/SQLitestaticPool 不支持 pool_size / max_overflow / pool_pre_ping，
    需按 url scheme 判断后再传参，确保测试环境（sqlite 内存库）也能正常启动。
    Supabase 需要的 TLS 与「关闭 prepared statement」由
    `app.db.engine_options` 统一提供（与 alembic 共用同一份逻辑）。
    """
    # 注意：`str(URL)` 会把密码打码成 `***`，必须显式 `hide_password=False`
    url = prepare_database_url().render_as_string(hide_password=False)
    common_kwargs = dict(
        echo=settings.database.echo,
        future=True,
    )

    if url.startswith("sqlite"):
        # SQLite：使用 StaticPool 让内存库在跨连接间共享
        from sqlalchemy.pool import StaticPool

        return create_async_engine(
            url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            **common_kwargs,
        )

    # PostgreSQL / MySQL 等远程数据库：启用连接池与 ping 检查
    return create_async_engine(
        url,
        pool_size=settings.database.pool_size,
        max_overflow=settings.database.max_overflow,
        pool_recycle=settings.database.pool_recycle,
        pool_pre_ping=True,  # 借出前 ping，避免拿到失效连接
        connect_args=database_connect_args(),
        **database_engine_kwargs(),
        **common_kwargs,
    )


# 全局 engine（应用生命周期内复用，避免反复建连）
engine = _build_engine()

# Session 工厂：每个请求/任务一个独立 Session
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖项：注入异步数据库 Session。

    用法：
        @router.get("/items")
        async def list_items(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            logger.exception("数据库会话异常，已回滚")
            raise
        finally:
            await session.close()
