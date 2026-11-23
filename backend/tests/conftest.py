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
# 测试专用 Redis DB（Issue #30）：此前指 db 0，与本地开发/巡检脚本共用——
# 限流桶（`ratelimit:*`）被测间累积，跑十几个"注册+登录"的用例就撞 429，
# 整批 task_api / recovery 用例长期红，看着像功能坏了。db 15 由本文件负责清空。
os.environ.setdefault("REDIS__URL", "redis://localhost:6379/15")
# HTTP 限流是**按 IP** 计数的部署态能力，测试客户端只有一个 IP：
# `auth_register` 默认 5 次/小时、`auth_login` 5 次/60 秒，一个文件里注册
# 十几个用户就撞 429（Issue #30：`test_task_api.py` 18 例全红、
# `test_login_lockout` 的「成功清零」用例也红）。
#
# 不关中间件（`test_rate_limit_middleware.py` 要测它），只把**高频动作**的
# 限额放大到不可能触发；账号维度的失败退避（`login_throttle`）是另一套机制，
# 不受影响，`test_login_lockout.py` 仍然测真实阈值。
for _rule in (
    "AUTH_LOGIN",
    "AUTH_REGISTER",
    "AUTH_OTP_REQUEST",
    "TASK_CREATE",
    "TASK_CONTROL",
    "SEARCH",
    "UPLOAD",
):
    os.environ.setdefault(f"RATE_LIMIT__RULES__{_rule}__LIMIT", "100000")
# 单测的向量后端固定走 chroma 内存模式（`chromadb` 是可选依赖 [chroma]，CI 装了它）：
# 生产默认是 pgvector，需要真实 PostgreSQL + pgvector，而测试库是 SQLite —— 不显式指定
# 会让每个 `init_kb()` 调用在构造 pgvector 后端时直接报「不能用 sqlite DSN」。
# 需要验证 pgvector 行为的用例自己 monkeypatch 切过去（见 test_readiness / backend_selection）。
os.environ.setdefault("RAG__VECTOR_BACKEND", "chroma")
# JWT 测试密钥
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-not-for-production")
# 用户自带模型凭据的加密主密钥（BYOK，Issue #15）：32 字节的 base64。
# 与 JWT 密钥**必须是两个**——轮换签名密钥不该让所有用户的 key 变成不可解。
os.environ.setdefault("SECRETS__ENCRYPTION_KEY", "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=")
# 定时重跑调度（Issue #27）：单测不启动常驻 tick 循环（它有自己的后台会话，
# 会去打真实 engine）。需要验证调度逻辑的用例直接构造 RoutineScheduler.tick()。
os.environ.setdefault("ROUTINE__ENABLED", "false")

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


@pytest.fixture(autouse=True)
def _reset_rate_limit_buckets():
    """每条用例前清空测试 Redis 的限流桶（best-effort，无 Redis 时静默跳过）。

    为什么必须做：限流桶是**跨用例共享**的外部状态，而触发限流的路径
    （注册 + 登录）在 `test_task_api.py` 里要跑十几次。不清就会在文件内部
    撞 429（实测 18 例里 18 例红），而单独跑一条又过——这类「只在文件级复现」
    的红最容易长期没人管（Issue #30）。

    用**同步**客户端：本夹具可能在事件循环之外运行，`asyncio.run` 在已有
    loop 时会抛错并被静默跳过（等于没清）。只 FLUSH 测试库（conftest 固定的
    db 15）；连不上说明本机没有 Redis，限流本身 fail-open，跳过即可。
    """
    try:
        from redis import Redis

        client = Redis.from_url(
            os.environ.get("REDIS__URL", ""),
            socket_connect_timeout=0.2,
            socket_timeout=0.2,
        )
        try:
            client.flushdb()
        finally:
            client.close()
    except Exception:  # 无 Redis / 连不上：限流 fail-open，不需要清理
        pass


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
