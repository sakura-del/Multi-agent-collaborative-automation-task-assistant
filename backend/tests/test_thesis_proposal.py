"""开题报告（Phase 2）：来源综述继承与提交校验。

覆盖：
- `TaskExecution._inherited_context` / `_format_inherited_context` 纯函数；
- `submit()` 对 `source_task_id` 的校验（不存在 / 他人 / 未完成 / 已完成写入 config）。
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.db.models  # noqa: F401  确保全部 ORM 注册到 Base.metadata
from app.core.exceptions import NotFoundException, ValidationException
from app.db.base import Base
from app.db.models.task import Task
from app.db.models.user import User
from app.orchestration.contracts import LiteratureReviewRequest, TaskStatus
from app.orchestration.events import InMemoryEventPublisher
from app.orchestration.runtime import RuntimeContainer
from app.orchestration.services.task_execution import TaskExecution

MakeSource = Callable[..., uuid.UUID]


class _Env:
    def __init__(
        self,
        *,
        client: TestClient,
        execution: TaskExecution,
        session_factory: async_sessionmaker[AsyncSession],
        user_ids: dict[str, uuid.UUID],
        make_source: MakeSource,
    ) -> None:
        self.client = client
        self.execution = execution
        self.session_factory = session_factory
        self.user_ids = user_ids
        self.make_source = make_source

    def submit(self, user: str, request: LiteratureReviewRequest) -> uuid.UUID:
        return self.client.portal.call(self.execution.submit, self.user_ids[user], request)

    def get_task(self, task_id: uuid.UUID) -> Task:
        async def _load() -> Task:
            async with self.session_factory() as session:
                task = await session.get(Task, task_id)
                assert task is not None
                return task

        return self.client.portal.call(_load)


@pytest.fixture
def proposal_env(tmp_path: Path) -> Iterator[_Env]:
    from app.main import app

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'proposal.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    container = RuntimeContainer(
        session_factory=session_factory,
        planner_factory=lambda: SimpleNamespace(),
        agent_factory=lambda _agent_type: SimpleNamespace(),
        event_publisher=InMemoryEventPublisher(),
    )
    execution = TaskExecution(container)

    async def noop_plan(_task_id: uuid.UUID) -> None:
        return None

    execution._plan = noop_plan  # type: ignore[method-assign]

    async def ensure_schema() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    user_ids: dict[str, uuid.UUID] = {}

    async def seed_users() -> None:
        await ensure_schema()
        async with session_factory() as session:
            for name in ("alice", "bob"):
                user = User(
                    username=name,
                    email=f"{name}@proposal.test",
                    password_hash="x",
                    is_active=True,
                )
                session.add(user)
                await session.flush()
                user_ids[name] = user.id
            await session.commit()

    client = TestClient(app)
    client.__enter__()
    client.portal.call(seed_users)

    def make_source(
        *,
        owner: str,
        status: str = TaskStatus.COMPLETED.value,
        review_report: dict[str, Any] | None = None,
    ) -> uuid.UUID:
        async def _create() -> uuid.UUID:
            async with session_factory() as session:
                task = Task(
                    user_id=user_ids[owner],
                    title="前置综述：检索增强生成",
                    description="",
                    task_type="literature_review",
                    status=status,
                    config={},
                    review_report=review_report,
                )
                session.add(task)
                await session.flush()
                task_id = task.id
                await session.commit()
                return task_id

        return client.portal.call(_create)

    env = _Env(
        client=client,
        execution=execution,
        session_factory=session_factory,
        user_ids=user_ids,
        make_source=make_source,
    )
    yield env

    client.portal.call(container.close)
    client.__exit__(None, None, None)
    asyncio.run(engine.dispose())


def _request(**overrides: Any) -> LiteratureReviewRequest:
    payload: dict[str, Any] = {
        "topic": "基于 STM32 的低功耗运动手环心率监测系统设计",
        "keywords": [],
        "workflow_preset": "thesis_proposal",
    }
    payload.update(overrides)
    return LiteratureReviewRequest(**payload)


def _report() -> dict[str, Any]:
    return {
        "evidence_matrix": {
            "questions": [
                "RAG 如何提升证据可靠性？",
                "多智能体协作如何影响综述质量？",
                "中文文献覆盖是否充分？",
            ],
            "cells": [
                {"question_index": 0, "bucket": "supported"},
                {"question_index": 1, "bucket": "weak_or_caveat"},
                {"question_index": 2, "bucket": "gap"},
            ],
        },
        "final_markdown": (
            "# 检索增强生成综述\n\n"
            "现有工作多聚焦英文语料，中文场景下的证据核验仍缺系统性评估。\n\n"
            "## 参考文献\n- [1] 示例"
        ),
    }


class TestInheritedContext:
    def test_extracts_gaps_and_baseline(self) -> None:
        source = SimpleNamespace(
            id=uuid.uuid4(),
            title="前置综述：检索增强生成",
            review_report=_report(),
            final_output=None,
        )

        context = TaskExecution._inherited_context(source)  # type: ignore[arg-type]

        assert context["source_task_id"] == str(source.id)
        assert context["source_title"] == "前置综述：检索增强生成"
        assert context["open_gaps"] == [
            "多智能体协作如何影响综述质量？",
            "中文文献覆盖是否充分？",
        ]
        assert context["baseline"].startswith("现有工作多聚焦英文语料")

    def test_returns_empty_when_report_missing(self) -> None:
        source = SimpleNamespace(
            id=uuid.uuid4(),
            title="空报告",
            review_report=None,
            final_output="",
        )

        context = TaskExecution._inherited_context(source)  # type: ignore[arg-type]

        assert context["open_gaps"] == []
        assert context["baseline"] == ""

    def test_formats_context_for_planner(self) -> None:
        text = TaskExecution._format_inherited_context(
            {
                "source_task_id": "x",
                "source_title": "前置综述",
                "open_gaps": ["问题 A", "问题 B"],
                "baseline": "原综述基线摘要",
            }
        )

        assert text.startswith("【继承自前置文献综述的研究空白与基线】")
        assert "待进一步研究的问题：问题 A；问题 B" in text
        assert "原综述基线：原综述基线摘要" in text

    def test_formats_empty_context_as_empty_string(self) -> None:
        assert TaskExecution._format_inherited_context(None) == ""
        assert TaskExecution._format_inherited_context({}) == ""


class TestSubmitSourceTaskId:
    def test_missing_source_raises_not_found(self, proposal_env: _Env) -> None:
        with pytest.raises(NotFoundException) as excinfo:
            proposal_env.submit("alice", _request(source_task_id=uuid.uuid4()))

        assert "来源综述任务不存在" in str(excinfo.value)

    def test_other_users_source_raises_not_found(self, proposal_env: _Env) -> None:
        source_id = proposal_env.make_source(owner="alice")

        with pytest.raises(NotFoundException):
            proposal_env.submit("bob", _request(source_task_id=source_id))

    def test_unfinished_source_raises_validation_error(self, proposal_env: _Env) -> None:
        source_id = proposal_env.make_source(owner="alice", status=TaskStatus.PLANNING.value)

        with pytest.raises(ValidationException) as excinfo:
            proposal_env.submit("alice", _request(source_task_id=source_id))

        assert "只有已完成的文献综述任务" in str(excinfo.value)

    def test_completed_source_writes_inherited_context(self, proposal_env: _Env) -> None:
        source_id = proposal_env.make_source(owner="alice", review_report=_report())

        created = proposal_env.submit("alice", _request(source_task_id=source_id))
        task = proposal_env.get_task(created)

        inherited = task.config["inherited_context"]
        assert inherited["source_task_id"] == str(source_id)
        assert len(inherited["open_gaps"]) == 2
        assert inherited["baseline"]
        assert "【继承自前置文献综述的研究空白与基线】" in task.description
        assert task.title == "基于 STM32 的低功耗运动手环心率监测系统设计"
