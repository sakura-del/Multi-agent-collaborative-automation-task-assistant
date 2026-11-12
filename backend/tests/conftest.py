"""pytest 公共夹具。

在导入应用之前设置测试环境变量，让测试无需依赖外部 .env 文件即可运行。
提供数据库 session 与 TestClient 夹具，供各测试模块复用。
"""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path

# 把 backend/ 加入 sys.path，便于 `from app...` 导入
BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

# ===== 测试环境变量（必须在 import app.* 之前设置）=====
# 使用 SQLite 内存库，避免依赖真实 PostgreSQL
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("APP_DEBUG", "false")
os.environ.setdefault("APP_LOG_FORMAT", "console")
# pydantic-settings 嵌套分隔符为 __ ，故 DATABASE__URL 映射到 settings.database.url
os.environ.setdefault("DATABASE__URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS__URL", "redis://localhost:6379/0")
# 单测的向量后端固定走 chroma 内存模式（`chromadb` 是可选依赖 [chroma]，CI 装了它）：
# 生产默认是 pgvector，需要真实 PostgreSQL + pgvector，而测试库是 SQLite —— 不显式指定
# 会让每个 `init_kb()` 调用在构造 pgvector 后端时直接报「不能用 sqlite DSN」。
# 需要验证 pgvector 行为的用例自己 monkeypatch 切过去（见 test_readiness / backend_selection）。
os.environ.setdefault("RAG__VECTOR_BACKEND", "chroma")
# JWT 测试密钥
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-not-for-production")

# 存储根目录指到临时目录。
# 生产配置里 `upload_root` / `artifact_root` 是**容器内绝对路径** `/app/data/...`，
# 而 GitHub runner 没有创建 `/app` 的权限 → `init_kb()` 一构造存储就 PermissionError。
# 这个坑只在 Linux CI 暴露：本地 Windows 上 `/app` 会落到 `C://app`，恰好能建。
# 用 setdefault：外部显式指定时以外部为准。
import atexit  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402

_TEST_DATA_ROOT = Path(tempfile.mkdtemp(prefix="veripaper-test-data-"))
atexit.register(lambda: shutil.rmtree(_TEST_DATA_ROOT, ignore_errors=True))
# 这两个字段在 `AppConfig` 下（不是 StorageConfig），环境变量名是 APP__UPLOAD_ROOT。
# 写成 STORAGE__... 不会有任何报错，只是**静默不生效**——实测过。
os.environ.setdefault("APP__UPLOAD_ROOT", str(_TEST_DATA_ROOT / "uploads"))
os.environ.setdefault("APP__ARTIFACT_ROOT", str(_TEST_DATA_ROOT / "artifacts"))

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db.base import Base  # noqa: E402


@pytest.fixture(scope="session")
def app_settings():
    """返回全局配置单例。"""
    from app.core.config import get_settings

    return get_settings()


@pytest.fixture(autouse=True)
def _isolate_artifact_root(tmp_path, monkeypatch):
    """把导出产物根目录指到临时目录，避免测试写入 /app/data/artifacts。"""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings().app, "artifact_root", str(tmp_path / "artifacts"))
    yield


@pytest_asyncio.fixture
async def db_engine():
    """每个测试函数创建独立的内存 SQLite engine，保证测试隔离。

    内存库随 engine 关闭即销毁，无需手动清理数据。
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        # SQLAlchemy 2.0 异步下需用 run_sync 执行 DDL
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine) -> AsyncIterator[AsyncSession]:
    """提供异步数据库 session，测试结束自动回滚（不污染其他测试）。"""
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
        # 测试后回滚，保证测试间数据隔离
        await session.rollback()


@pytest.fixture
def client():
    """同步 TestClient，用于端点测试。

    覆盖 get_db 依赖为内存 SQLite，避免依赖真实 PostgreSQL。
    """
    from fastapi.testclient import TestClient

    from app.db.session import get_db
    from app.main import app

    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    test_session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    _tables_created = False

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        nonlocal _tables_created
        if not _tables_created:
            async with test_engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            _tables_created = True
        async with test_session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
