"""审计日志服务（Issue #25 沉淀项 3）。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.db.models  # noqa: F401
from app.db.base import Base
from app.db.models.audit import AuditLog
from app.security.audit import record_audit, request_ip


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


def _request(
    headers: dict[str, str] | None = None, client: tuple[str, int] | None = ("1.2.3.4", 5)
):
    return SimpleNamespace(
        headers=headers or {}, client=SimpleNamespace(host=client[0]) if client else None
    )


async def test_record_writes_row_and_snapshots_actor(factory) -> None:
    actor_id = uuid.uuid4()
    async with factory() as session:
        await record_audit(
            session,
            actor_id=actor_id,
            actor_name="alice",
            action="credential.create",
            target_type="credential",
            target_id="cid-1",
            metadata={"provider": "deepseek"},
            request=_request(),
        )
    async with factory() as session:
        row = (await session.execute(select(AuditLog))).scalars().one()
    assert row.actor_name == "alice"
    assert row.action == "credential.create"
    assert row.target_id == "cid-1"
    assert row.metadata_json == {"provider": "deepseek"}
    assert row.ip == "1.2.3.4"


async def test_record_failure_isolated_by_savepoint(factory) -> None:
    """审计写失败只记日志；调用方会话仍可用（否则端点读对象会 500）。

    用不可 JSON 序列化的 metadata 触发 flush 失败。
    """
    actor_id = uuid.uuid4()
    async with factory() as session:
        await record_audit(
            session,
            actor_id=actor_id,
            actor_name="alice",
            action="credential.create",
            target_type="credential",
            metadata={"bad": object()},  # 无法 JSON 编码 → INSERT 失败
        )
        # 会话未被整体回滚：还能继续查询（业务对象不会因 rollback 过期）
        assert (await session.execute(select(AuditLog))).scalars().all() == []


def test_request_ip_prefers_socket_when_not_trusting_proxy() -> None:
    from app.core.config import settings

    assert request_ip(_request({"x-real-ip": "9.9.9.9"})) == "1.2.3.4"
    original = settings.rate_limit.trust_proxy
    try:
        settings.rate_limit.trust_proxy = True
        assert request_ip(_request({"x-real-ip": "9.9.9.9"})) == "9.9.9.9"
    finally:
        settings.rate_limit.trust_proxy = original
