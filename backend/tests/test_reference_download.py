"""参考文献下载接口（Issue #4 M2 / 计划 §1.3）：`.bib` 与 `.ris`。

M1 已实现 `exporters.to_bibtex()/to_ris()`（纯字符串、零依赖），M2 把它接成下载接口：

    GET /api/v1/tasks/{task_id}/references.bib
    GET /api/v1/tasks/{task_id}/references.ris

要求：
- owner-scoped：跨用户/不存在一律 404（与既有产物下载同语义）；
- 内容来自该任务**已持久化**的 `review_report.bibliography`（不重新跑检索）；
- 任务未完成/无参考文献 → 400 且说明原因（不返回空文件让人困惑）；
- 文件名与 media type 正确（BibTeX `application/x-bibtex`，RIS `application/x-research-info-systems`）；
- **中文内容原样输出**（UTF-8，不转义、不乱码）。
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from functools import partial
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.db.models  # noqa: F401  确保全部 ORM 注册到 Base.metadata
from app.db.base import Base
from app.db.models.task import Task
from app.db.session import get_db
from app.orchestration.agents.planner import PlannerAgent
from app.orchestration.contracts import TaskStatus
from app.orchestration.events import InMemoryEventPublisher
from app.orchestration.runtime import RuntimeContainer
from app.orchestration.services.task_execution import TaskExecution

BIBLIOGRAPHY = [
    {
        "index": 1,
        "title": "Ragas: Automated Evaluation of Retrieval Augmented Generation",
        "source_url": "https://arxiv.org/abs/2309.15217v2",
        "authors": ["Shahul Es", "Jithin James"],
        "year": 2023,
        "language": "en-US",
    },
    {
        "index": 2,
        "title": "面向学位论文的中文参考文献著录规则研究",
        "source_url": "https://doi.org/10.1234/zh.journal.2024.001",
        "authors": ["牛耕田", "李泽"],
        "year": 2024,
        "language": "zh-CN",
    },
]


@pytest.fixture
def api_env(tmp_path: Path) -> Iterator[Any]:
    """独立文件型 SQLite 的 TestClient（与 test_task_api.py 同构）。"""
    from app.main import app

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'download-api.db'}",
        connect_args={"timeout": 10},
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    state = {"created": False}

    async def ensure_schema() -> None:
        """幂等建表（首次用到 session 时创建，供 seed_task 与 make_user 共用）。"""
        if state["created"]:
            return
        async with engine.begin() as connection:
            await connection.exec_driver_sql("PRAGMA journal_mode=WAL")
            await connection.run_sync(Base.metadata.create_all)
        state["created"] = True

    async def override_get_db():
        await ensure_schema()
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    client.__enter__()

    container = RuntimeContainer(
        session_factory=session_factory,
        planner_factory=lambda: PlannerAgent(),
        agent_factory=lambda agent_type: None,
        event_publisher=InMemoryEventPublisher(),
    )
    app.state.container = container
    app.state.runtime = TaskExecution(container)

    async def seed_task(
        user_id: uuid.UUID,
        *,
        status: str = TaskStatus.COMPLETED.value,
        bibliography: list[dict[str, Any]] | None = BIBLIOGRAPHY,
    ) -> uuid.UUID:
        """直接写一条已带 review_report 的任务（不跑执行链路）。"""
        await ensure_schema()
        async with session_factory() as session:
            task = Task(
                user_id=user_id,
                title="参考文献下载测试",
                description="下载接口契约",
                task_type="literature_review",
                status=status,
                config={},
                review_report={"bibliography": bibliography} if bibliography is not None else None,
            )
            session.add(task)
            await session.commit()
            return task.id

    def make_user(username: str) -> tuple[uuid.UUID, dict[str, str]]:
        """建用户并签发 access token。"""
        from app.core.security import create_access_token
        from app.db.models.user import User

        async def _create() -> uuid.UUID:
            await ensure_schema()
            async with session_factory() as session:
                user = User(
                    username=username,
                    email=f"{username}@example.com",
                    password_hash="x",
                    role="user",
                    is_active=True,
                )
                session.add(user)
                await session.commit()
                return user.id

        user_id = client.portal.call(_create)
        token = create_access_token(str(user_id))
        return user_id, {"Authorization": f"Bearer {token}"}

    yield client, seed_task, make_user

    try:
        client.portal.call(container.close)
    except Exception:
        pass
    client.__exit__(None, None, None)
    app.dependency_overrides.clear()
    asyncio.run(engine.dispose())


class TestBibtexDownload:
    def test_downloads_bibtex_with_metadata(self, api_env: Any) -> None:
        client, seed_task, make_user = api_env
        user_id, headers = make_user("alice")
        task_id = client.portal.call(seed_task, user_id)

        response = client.get(f"/api/v1/tasks/{task_id}/references.bib", headers=headers)

        assert response.status_code == 200
        assert "x-bibtex" in response.headers["content-type"]
        text = response.content.decode("utf-8")
        assert "@article" in text or "@misc" in text
        assert "Ragas" in text
        assert "2023" in text

    def test_bibtex_keeps_chinese_verbatim(self, api_env: Any) -> None:
        client, seed_task, make_user = api_env
        user_id, headers = make_user("alice")
        task_id = client.portal.call(seed_task, user_id)

        response = client.get(f"/api/v1/tasks/{task_id}/references.bib", headers=headers)

        text = response.content.decode("utf-8")
        assert "面向学位论文的中文参考文献著录规则研究" in text
        assert "牛耕田" in text

    def test_download_name_is_stable(self, api_env: Any) -> None:
        client, seed_task, make_user = api_env
        user_id, headers = make_user("alice")
        task_id = client.portal.call(seed_task, user_id)

        response = client.get(f"/api/v1/tasks/{task_id}/references.bib", headers=headers)

        assert "attachment" in response.headers.get("content-disposition", "")
        assert ".bib" in response.headers.get("content-disposition", "")


class TestRisDownload:
    def test_downloads_ris(self, api_env: Any) -> None:
        client, seed_task, make_user = api_env
        user_id, headers = make_user("alice")
        task_id = client.portal.call(seed_task, user_id)

        response = client.get(f"/api/v1/tasks/{task_id}/references.ris", headers=headers)

        assert response.status_code == 200
        assert "research-info-systems" in response.headers["content-type"]
        text = response.content.decode("utf-8")
        assert "TY  -" in text
        assert "ER  -" in text
        assert "Ragas" in text

    def test_ris_keeps_chinese_verbatim(self, api_env: Any) -> None:
        client, seed_task, make_user = api_env
        user_id, headers = make_user("alice")
        task_id = client.portal.call(seed_task, user_id)

        response = client.get(f"/api/v1/tasks/{task_id}/references.ris", headers=headers)

        assert "面向学位论文的中文参考文献著录规则研究" in response.content.decode("utf-8")


class TestDownloadAccessControl:
    def test_requires_authentication(self, api_env: Any) -> None:
        client, seed_task, make_user = api_env
        user_id, _headers = make_user("alice")
        task_id = client.portal.call(seed_task, user_id)

        assert client.get(f"/api/v1/tasks/{task_id}/references.bib").status_code == 401

    def test_other_user_gets_404(self, api_env: Any) -> None:
        """跨用户与不存在同语义。"""
        client, seed_task, make_user = api_env
        owner_id, _ = make_user("alice")
        _other_id, other_headers = make_user("bob")
        task_id = client.portal.call(seed_task, owner_id)

        assert (
            client.get(f"/api/v1/tasks/{task_id}/references.bib", headers=other_headers).status_code
            == 404
        )

    def test_unknown_task_gets_404(self, api_env: Any) -> None:
        client, _seed_task, make_user = api_env
        _user_id, headers = make_user("alice")

        assert (
            client.get(f"/api/v1/tasks/{uuid.uuid4()}/references.bib", headers=headers).status_code
            == 404
        )


class TestDownloadPreconditions:
    def test_task_without_bibliography_returns_400(self, api_env: Any) -> None:
        """没有参考文献时给明确错误，不返回空文件。"""
        client, seed_task, make_user = api_env
        user_id, headers = make_user("alice")
        task_id = client.portal.call(partial(seed_task, user_id, bibliography=None))

        response = client.get(f"/api/v1/tasks/{task_id}/references.bib", headers=headers)

        assert response.status_code == 400
        assert "参考文献" in response.json()["detail"]

    def test_empty_bibliography_returns_400(self, api_env: Any) -> None:
        client, seed_task, make_user = api_env
        user_id, headers = make_user("alice")
        task_id = client.portal.call(partial(seed_task, user_id, bibliography=[]))

        assert (
            client.get(f"/api/v1/tasks/{task_id}/references.ris", headers=headers).status_code
            == 400
        )
