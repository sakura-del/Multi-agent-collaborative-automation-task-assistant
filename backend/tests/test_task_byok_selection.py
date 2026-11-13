"""任务级模型选择测试（Issue #15 · P0-3）。

## 本文件锁定

1. **凭据先校验、再扣篇数**：拿别人的/停用的凭据建任务，必须 404/403，
   且**不能**白扣一次篇数；
2. **规划与执行都用用户模型**：`_plan` 在建 planner 之前绑定作用域，
   planner 实际看到的 LLM 必须是 BYOK 客户端；
3. **凭据被删不静默回退**：外键 `SET NULL` 后快照仍在 → 任务转人工并说明原因，
   而不是偷偷用平台模型；
4. 快照带上模型信息（名称/厂商/模型），凭据删除后仍可追溯；
5. 不带凭据时保持旧调用形状（编排层替身无需感知 BYOK 参数）。
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.db.models  # noqa: F401
from app.api.v1.endpoints import tasks as tasks_endpoint
from app.core.exceptions import ForbiddenException, LLMException, NotFoundException
from app.db.base import Base
from app.db.models.task import Task
from app.db.models.user import User
from app.llm.scope import get_scoped_llm
from app.orchestration.agents.planner import PlannerAgent
from app.orchestration.base_agent import BaseAgent
from app.orchestration.contracts import LiteratureReviewRequest, TaskPlan, TaskStatus
from app.orchestration.runtime import RuntimeContainer
from app.orchestration.services.task_execution import TaskExecution
from app.schemas.llm_credential import LLMCredentialCreate
from app.schemas.task import TaskCreate
from app.security.credential_cipher import CredentialCipher
from app.security.llm_credential_service import LLMCredentialService


def _plan_payload() -> dict[str, Any]:
    return {
        "version": 1,
        "topic": "检索增强生成证据核验",
        "research_questions": [
            "RAG 如何提升证据可靠性？",
            "如何度量可追溯性？",
            "多智能体如何影响质量？",
        ],
        "search_queries": ["RAG evidence verification"],
        "acceptance_criteria": ["引用可回溯"],
        "nodes": [
            {"id": "n1", "type": "search", "title": "检索文献", "dependencies": []},
            {"id": "n2", "type": "extract", "title": "提取证据", "dependencies": ["n1"]},
            {"id": "n3", "type": "write", "title": "撰写综述", "dependencies": ["n2"]},
            {"id": "n4", "type": "validate", "title": "校验引用", "dependencies": ["n3"]},
            {"id": "n5", "type": "review", "title": "汇总复盘", "dependencies": ["n4"]},
        ],
    }


class _CapturePlanner(BaseAgent):
    """记录规划时实际拿到的 LLM（用于断言作用域已生效）。"""

    def __init__(self) -> None:
        super().__init__()
        self.seen_llm: Any | None = None
        self.calls = 0

    @property
    def agent_type(self) -> str:
        return "planner"

    async def _run(self, input_data: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        self.seen_llm = get_scoped_llm()
        plan = TaskPlan.model_validate(_plan_payload())
        return {
            "plan": plan.model_dump(mode="json"),
            "dag": PlannerAgent.build_dag(plan).to_dict(),
        }


class _RecordingExecution:
    """记录 submit 的完整调用（含 BYOK 关键字参数）。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def submit(self, user_id: Any, request: Any, **kwargs: Any) -> uuid.UUID:
        self.calls.append({"user_id": user_id, "request": request, **kwargs})
        return uuid.uuid4()


class _RecordingPlanService:
    def __init__(self) -> None:
        self.calls = 0

    async def check_and_consume_review(self, user_id: Any) -> None:
        self.calls += 1


@pytest.fixture
async def factory(tmp_path: Any) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """文件型 SQLite：后台任务与会话需要独立连接（内存库会串事务）。"""
    db_path = tmp_path / "task-byok.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path.as_posix()}", connect_args={"timeout": 10}
    )
    async with engine.begin() as connection:
        await connection.exec_driver_sql("PRAGMA journal_mode=WAL")
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _make_user(factory: async_sessionmaker[AsyncSession], name: str) -> User:
    async with factory() as session:
        user = User(username=name, email=f"{name}@example.com", password_hash="x", is_active=True)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _make_credential(
    factory: async_sessionmaker[AsyncSession],
    user: User,
    *,
    name: str = "我的 DeepSeek",
    provider: str = "deepseek",
    model: str = "deepseek-chat",
) -> Any:
    async with factory() as session:
        service = LLMCredentialService(cipher=CredentialCipher.from_settings())
        return await service.create(
            session,
            user.id,
            LLMCredentialCreate(
                name=name,
                provider=provider,  # type: ignore[arg-type]
                model=model,
                api_key="sk-user-key-1234567890",
            ),
        )


def _snapshot(record: Any) -> dict[str, Any]:
    return {
        "credential_id": str(record.id),
        "name": record.name,
        "provider": record.provider,
        "model": record.model,
        "base_url": record.base_url,
        "is_byok": True,
    }


def _request() -> LiteratureReviewRequest:
    return LiteratureReviewRequest(
        topic="检索增强生成证据核验", keywords=["RAG", "证据核验"], max_documents=5
    )


def _execution(factory: async_sessionmaker[AsyncSession], planner: BaseAgent) -> TaskExecution:
    container = RuntimeContainer(
        session_factory=factory,
        planner_factory=lambda: planner,
    )
    return TaskExecution(container)


async def _wait_for_status(
    execution: TaskExecution, user_id: uuid.UUID, task_id: uuid.UUID, expected: set[str]
) -> Any:
    deadline = asyncio.get_running_loop().time() + 8.0
    last = None
    while asyncio.get_running_loop().time() < deadline:
        snapshot = await execution.get_snapshot(user_id, task_id)
        last = snapshot.status
        if snapshot.status in expected:
            return snapshot
        await asyncio.sleep(0.01)
    raise AssertionError(f"等待状态 {expected} 超时，当前: {last}")


class TestContract:
    def test_llm_credential_not_forwarded_to_orchestration(self) -> None:
        """`llm_credential_id` 是归属/计费信息，编排层合约不认它（extra=forbid）。"""
        payload = TaskCreate(
            topic="检索增强生成证据核验",
            keywords=["RAG"],
            llm_credential_id=uuid.uuid4(),
        )
        contract = payload.to_contract()
        assert not hasattr(contract, "llm_credential_id")


class TestEndpointCreate:
    async def test_passes_credential_and_snapshot(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        user = await _make_user(factory, "byok_task_owner")
        credential = await _make_credential(factory, user)
        execution = _RecordingExecution()
        plan = _RecordingPlanService()

        async with factory() as session:
            out = await tasks_endpoint.create_task(
                payload=TaskCreate(
                    topic="检索增强生成证据核验",
                    keywords=["RAG"],
                    llm_credential_id=credential.id,
                ),
                execution=execution,  # type: ignore[arg-type]
                current_user=user,
                _scope=None,  # type: ignore[arg-type]
                plan_service=plan,  # type: ignore[arg-type]
                db=session,
                quota=None,
            )

        assert out.status == TaskStatus.PLANNING.value
        assert plan.calls == 1
        call = execution.calls[0]
        assert call["llm_credential_id"] == credential.id
        assert call["model_snapshot"]["model"] == "deepseek-chat"
        assert call["model_snapshot"]["is_byok"] is True

    async def test_cross_user_credential_is_404_and_burns_no_review(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        alice = await _make_user(factory, "byok_task_alice")
        bob = await _make_user(factory, "byok_task_bob")
        bob_credential = await _make_credential(factory, bob)
        execution = _RecordingExecution()
        plan = _RecordingPlanService()

        async with factory() as session:
            with pytest.raises(NotFoundException):
                await tasks_endpoint.create_task(
                    payload=TaskCreate(
                        topic="检索增强生成证据核验",
                        keywords=["RAG"],
                        llm_credential_id=bob_credential.id,
                    ),
                    execution=execution,  # type: ignore[arg-type]
                    current_user=alice,
                    _scope=None,  # type: ignore[arg-type]
                    plan_service=plan,  # type: ignore[arg-type]
                    db=session,
                    quota=None,
                )

        # 凭据检查在扣篇数之前：不能因为选错 key 白扣一次额度
        assert plan.calls == 0
        assert execution.calls == []

    async def test_disabled_credential_is_403(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        user = await _make_user(factory, "byok_task_disabled")
        credential = await _make_credential(factory, user)
        async with factory() as session:
            row = await session.get(type(credential), credential.id)
            assert row is not None
            row.status = "disabled"
            await session.commit()

        execution = _RecordingExecution()
        plan = _RecordingPlanService()
        async with factory() as session:
            with pytest.raises(ForbiddenException):
                await tasks_endpoint.create_task(
                    payload=TaskCreate(
                        topic="检索增强生成证据核验",
                        keywords=["RAG"],
                        llm_credential_id=credential.id,
                    ),
                    execution=execution,  # type: ignore[arg-type]
                    current_user=user,
                    _scope=None,  # type: ignore[arg-type]
                    plan_service=plan,  # type: ignore[arg-type]
                    db=session,
                    quota=None,
                )

        assert plan.calls == 0
        assert execution.calls == []

    async def test_without_credential_keeps_old_call_shape(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """兼容：不带凭据时 submit 仍是两参数调用，编排层替身无需改签名。"""
        user = await _make_user(factory, "byok_task_plain")
        execution = _RecordingExecution()
        plan = _RecordingPlanService()

        async with factory() as session:
            await tasks_endpoint.create_task(
                payload=TaskCreate(topic="检索增强生成证据核验", keywords=["RAG"]),
                execution=execution,  # type: ignore[arg-type]
                current_user=user,
                _scope=None,  # type: ignore[arg-type]
                plan_service=plan,  # type: ignore[arg-type]
                db=session,
                quota=None,
            )

        assert set(execution.calls[0]) == {"user_id", "request"}


class TestPlanningScope:
    async def test_plan_uses_user_client(self, factory: async_sessionmaker[AsyncSession]) -> None:
        user = await _make_user(factory, "byok_plan_scope")
        credential = await _make_credential(factory, user)
        planner = _CapturePlanner()
        execution = _execution(factory, planner)

        task_id = await execution.submit(
            user.id,
            _request(),
            llm_credential_id=credential.id,
            model_snapshot=_snapshot(credential),
        )
        snapshot = await _wait_for_status(
            execution, user.id, task_id, {TaskStatus.WAITING_APPROVAL.value}
        )

        assert planner.calls == 1
        assert planner.seen_llm is not None
        assert planner.seen_llm.is_byok is True
        assert planner.seen_llm.credential_id == credential.id
        assert planner.seen_llm.model == "deepseek-chat"
        assert snapshot.model is not None
        assert snapshot.model.credential_id == credential.id
        assert snapshot.model.model == "deepseek-chat"

    async def test_platform_task_has_no_model_snapshot(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        user = await _make_user(factory, "byok_plan_platform")
        planner = _CapturePlanner()
        execution = _execution(factory, planner)

        task_id = await execution.submit(user.id, _request())
        snapshot = await _wait_for_status(
            execution, user.id, task_id, {TaskStatus.WAITING_APPROVAL.value}
        )

        assert snapshot.model is None
        # 作用域没被打开：planner 拿到的是容器默认客户端（平台），不是 BYOK
        assert getattr(planner.seen_llm, "is_byok", False) is False

    async def test_deleted_credential_fails_instead_of_falling_back(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """外键 SET NULL 后不能静默用平台模型。"""
        user = await _make_user(factory, "byok_plan_deleted")
        execution = _execution(factory, _CapturePlanner())
        orphan = SimpleNamespace(
            id=uuid.uuid4(),
            user_id=user.id,
            llm_credential_id=None,
            model_snapshot={"is_byok": True, "model": "deepseek-chat"},
        )

        with pytest.raises(LLMException) as exc:
            await execution._bind_task_llm(orphan)

        assert "已被删除" in exc.value.message

    async def test_disabled_after_creation_marks_waiting_human(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        user = await _make_user(factory, "byok_plan_disabled")
        credential = await _make_credential(factory, user)
        async with factory() as session:
            row = await session.get(type(credential), credential.id)
            assert row is not None
            row.status = "disabled"
            await session.commit()

        execution = _execution(factory, _CapturePlanner())
        task_id = await execution.submit(
            user.id,
            _request(),
            llm_credential_id=credential.id,
            model_snapshot=_snapshot(credential),
        )
        snapshot = await _wait_for_status(
            execution, user.id, task_id, {TaskStatus.WAITING_HUMAN.value}
        )

        # 失败但保留快照：用户能看出「是这把凭据不可用」，而不是任务凭空消失
        assert snapshot.model is not None
        assert snapshot.model.credential_id == credential.id


class TestPersistence:
    async def test_task_row_stores_credential_and_snapshot(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        user = await _make_user(factory, "byok_persist")
        credential = await _make_credential(factory, user, name="实验室共享 key")
        execution = _execution(factory, _CapturePlanner())

        task_id = await execution.submit(
            user.id,
            _request(),
            llm_credential_id=credential.id,
            model_snapshot=_snapshot(credential),
        )
        await _wait_for_status(execution, user.id, task_id, {TaskStatus.WAITING_APPROVAL.value})

        async with factory() as session:
            task = await session.get(Task, task_id)
            assert task is not None
            assert task.llm_credential_id == credential.id
            assert task.model_snapshot is not None
            assert task.model_snapshot["name"] == "实验室共享 key"
            # 凭据的 key 绝不能出现在任务行里
            assert "sk-user-key-1234567890" not in str(task.model_snapshot)
            assert "ciphertext" not in str(task.model_snapshot)
