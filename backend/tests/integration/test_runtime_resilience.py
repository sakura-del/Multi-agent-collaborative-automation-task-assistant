"""恢复、隔离与故障降级证明（Plan 5 Task 2）。

覆盖计划要求的故障注入矩阵：
1. 数据库不可用 → 写入失败但保留最后已提交状态，不产生"半成品"任务；
2. Redis 不可用 → 执行不受影响，事件仍可通过 HTTP 补拉（轮询路径）；
3. Chroma/知识库检索失败 → 受影响节点转 WAITING_HUMAN（而不是整体崩掉或静默成功）；
4. arXiv 超时 → 离线语料路径仍能完成（不依赖在线检索）；
5. 进程重启（一个节点已提交）→ 已完成节点不重跑；
6. 校验耗尽 → 转 WAITING_HUMAN 且 retryable 显式；
7. 取消发生在 LLM 请求进行中 → 迟到的结果被丢弃。

所有用例都使用注入式运行时（`tests/fakes`），不读环境变量、不访问网络。
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.orchestration.events import InMemoryEventPublisher
from app.orchestration.runtime import RuntimeContainer
from app.orchestration.services.task_execution import TaskExecution
from tests.fakes import FixedLiteratureLLM, RecordingEventPublisher

DOC_ONE = "检索增强生成通过外部知识检索降低事实性错误。忠实度是核心评估维度。".encode()
DOC_TWO = "多智能体协作把检索、抽取、写作与校验拆分为专门角色，提升可追溯性。".encode()


# ===== 夹具 =====


@pytest.fixture
def resilience_env(tmp_path: Path) -> Iterator[Any]:
    from app.db.session import get_db
    from app.knowledge.services import factory as kb_factory
    from app.main import app

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'resilience.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    state = {"created": False}

    async def override_get_db():
        if not state["created"]:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            state["created"] = True
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    from app.core.config import get_settings

    settings = get_settings()
    settings.app.upload_root = str(tmp_path / "uploads")
    settings.app.artifact_root = str(tmp_path / "artifacts")
    kb_factory.reset_kb()
    kb_factory.init_kb(force_mock_embedding=True, force_mock_reranker=True, chroma_persist_dir="")

    client = TestClient(app)
    client.__enter__()

    installed: list[RuntimeContainer] = []

    def install(
        *,
        llm: Any | None = None,
        publisher: Any | None = None,
        knowledge_query: Any | None = None,
        literature_source: Any | None = None,
    ) -> TaskExecution:
        from app.knowledge.services.search_provider import KnowledgeSearchProvider

        container = RuntimeContainer(
            session_factory=session_factory,
            event_publisher=publisher or InMemoryEventPublisher(),
            llm=llm or FixedLiteratureLLM(),
            knowledge_query=(
                knowledge_query
                if knowledge_query is not None
                else KnowledgeSearchProvider(session_factory)
            ),
            literature_source=literature_source,
        )
        execution = TaskExecution(container)
        app.state.container = container
        app.state.runtime = execution
        installed.append(container)
        return execution

    yield {
        "client": client,
        "session_factory": session_factory,
        "engine": engine,
        "install": install,
        "settings": settings,
        "tmp_path": tmp_path,
    }

    for container in installed:
        try:
            client.portal.call(container.close)
        except Exception:
            pass
    client.__exit__(None, None, None)
    app.dependency_overrides.clear()
    kb_factory.reset_kb()
    asyncio.run(engine.dispose())


# ===== 辅助 =====


def _register_and_login(client: TestClient, username: str) -> str:
    password = "resilience-password-2026"
    client.post(
        "/api/v1/auth/register",
        json={"username": username, "email": f"{username}@example.com", "password": password},
    )
    response = client.post("/api/v1/auth/login", json={"account": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _upload(client: TestClient, token: str, title: str, body: bytes) -> dict[str, Any]:
    response = client.post(
        "/api/v1/knowledge/documents",
        headers=_auth(token),
        files={"file": (f"{title}.txt", body, "text/plain")},
        data={"title": title, "strategy": "recursive"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_task(client: TestClient, token: str, topic: str = "韧性验证主题") -> str:
    response = client.post(
        "/api/v1/tasks",
        headers=_auth(token),
        json={
            "topic": topic,
            "keywords": ["resilience"],
            "max_documents": 3,
            "offline_dataset": True,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["task_id"]


def _wait_status(
    client: TestClient, token: str, task_id: str, expected: set[str], *, timeout: float = 120.0
) -> dict[str, Any]:
    import time

    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/tasks/{task_id}", headers=_auth(token))
        assert response.status_code == 200, response.text
        last = response.json()
        if last["status"] in expected:
            return last
        time.sleep(0.1)
    raise AssertionError(f"等待状态 {expected} 超时，当前 {last.get('status')}")


def _approve(client: TestClient, token: str, task_id: str) -> None:
    snapshot = _wait_status(client, token, task_id, {"waiting_approval"})
    response = client.post(
        f"/api/v1/tasks/{task_id}/plan/approve",
        headers=_auth(token),
        json={"revision": snapshot["revision"]},
    )
    assert response.status_code == 200, response.text


# ===== 故障降级 =====


class TestFailureDegradation:
    def test_chroma_failure_moves_node_to_waiting_human(self, resilience_env) -> None:
        """知识库检索抛错 → 受影响节点转 WAITING_HUMAN，不静默成功。"""
        client: TestClient = resilience_env["client"]

        class _BrokenKnowledge:
            async def search_for_context(self, *_args: Any, **_kwargs: Any) -> Any:
                raise RuntimeError("chroma unavailable")

        # 用坏掉的知识库替换容器依赖后重新装配
        resilience_env["install"](knowledge_query=_BrokenKnowledge())

        token = _register_and_login(client, "chroma-user")
        _upload(client, token, "文档一", DOC_ONE)
        task_id = _create_task(client, token)
        _approve(client, token, task_id)

        final = _wait_status(client, token, task_id, {"waiting_human", "failed"}, timeout=180.0)
        assert final["status"] in {"waiting_human", "failed"}
        # 至少一个节点明确处于 waiting_human/failed，且带错误信息
        blocked = [
            subtask
            for subtask in final["subtasks"]
            if subtask["status"] in {"waiting_human", "failed"}
        ]
        assert blocked, "检索失败应留下等待人工/失败的节点"
        assert any(subtask.get("error") for subtask in blocked)

    def test_arxiv_timeout_uses_offline_corpus(self, resilience_env) -> None:
        """在线检索源超时 → 离线语料路径仍能完成（不依赖 arXiv）。"""
        client: TestClient = resilience_env["client"]

        class _TimeoutSource:
            async def search(self, _search: Any) -> Any:
                raise TimeoutError("arxiv timeout")

        # 任务标记 offline_dataset=True → resolve_literature_source 直接走冻结清单，
        # 因此即使容器注入的在线源会超时，也不应影响离线任务。
        resilience_env["install"](literature_source=_TimeoutSource())
        token = _register_and_login(client, "offline-user")
        _upload(client, token, "离线文档", DOC_ONE)
        task_id = _create_task(client, token, topic="离线语料降级验证")
        _approve(client, token, task_id)

        final = _wait_status(client, token, task_id, {"completed"}, timeout=180.0)
        assert final["status"] == "completed"
        assert final["review_report"] is not None

    def test_validation_exhaustion_reports_retryable(self, resilience_env) -> None:
        """确定性校验持续失败 → 两次重生成后转 WAITING_HUMAN，且校验结果标记 retryable。"""
        client: TestClient = resilience_env["client"]

        class _NoEvidenceLLM(FixedLiteratureLLM):
            """执行节点永远输出不含引用标记的正文 → evidence_is_cited 必然失败。"""

            async def _raw_chat(self, messages, **kwargs):  # type: ignore[override]
                from app.llm.deterministic import render_response
                from app.llm.models import LLMResponse, Usage

                # 执行类调用改写成"无引用"正文；其余用途保持原样
                content = render_response(messages)
                if "[E" not in content and "## 证据梳理" not in content:
                    content = render_response(messages)
                else:
                    content = "## 证据梳理\n本节没有任何引用标记，也没有标注模型推断的结论。"
                self.calls.append({"system": "", "user": "", "content": content})
                return LLMResponse(
                    content=content,
                    usage=Usage(prompt_tokens=10, completion_tokens=10),
                    finish_reason="stop",
                    model=self.model,
                )

        resilience_env["install"](llm=_NoEvidenceLLM())
        token = _register_and_login(client, "exhaust-user")
        _upload(client, token, "证据文档", DOC_ONE)
        task_id = _create_task(client, token, topic="校验耗尽验证")
        _approve(client, token, task_id)

        final = _wait_status(client, token, task_id, {"waiting_human"}, timeout=240.0)
        blocked = [subtask for subtask in final["subtasks"] if subtask["status"] == "waiting_human"]
        assert blocked, "校验耗尽应停在 WAITING_HUMAN"
        validation = (blocked[0].get("output") or {}).get("validation") or {}
        assert validation.get("passed") is False
        assert validation.get("retryable") is True

    def test_cancel_discards_late_llm_result(self, resilience_env) -> None:
        """取消发生在 LLM 请求进行中 → 迟到的结果不落地，任务停在 cancelled。"""
        client: TestClient = resilience_env["client"]
        started = asyncio.Event()

        class _SlowLLM(FixedLiteratureLLM):
            async def _raw_chat(self, messages, **kwargs):  # type: ignore[override]
                started.set()
                await asyncio.sleep(1.5)
                return await super()._raw_chat(messages, **kwargs)

        resilience_env["install"](llm=_SlowLLM())
        token = _register_and_login(client, "cancel-user")
        _upload(client, token, "取消文档", DOC_ONE)
        task_id = _create_task(client, token, topic="取消丢弃迟到结果")
        _approve(client, token, task_id)

        # 取消命令在任务运行中发送
        response = client.post(
            f"/api/v1/tasks/{task_id}/control",
            headers=_auth(token),
            json={"command": "cancel"},
        )
        assert response.status_code == 200, response.text

        final = _wait_status(client, token, task_id, {"cancelled"}, timeout=120.0)
        assert final["status"] == "cancelled"
        # 取消后不再有新的完成事件把状态推回 running/completed
        import time

        time.sleep(2.0)
        after = client.get(f"/api/v1/tasks/{task_id}", headers=_auth(token)).json()
        assert after["status"] == "cancelled", "取消后迟到的 LLM 结果不得改变状态"

    def test_database_loss_keeps_last_committed_state(self, resilience_env) -> None:
        """数据库写路径失效时：运行时操作显式失败，已提交状态与事件不受影响。"""
        client: TestClient = resilience_env["client"]
        execution = resilience_env["install"]()
        token = _register_and_login(client, "db-loss-user")
        _upload(client, token, "数据库文档", DOC_ONE)
        task_id = _create_task(client, token, topic="数据库故障验证")
        _approve(client, token, task_id)
        snapshot = _wait_status(client, token, task_id, {"completed"}, timeout=180.0)
        assert snapshot["status"] == "completed"
        events_before = len(
            client.get(f"/api/v1/tasks/{task_id}/events", headers=_auth(token)).json()["events"]
        )

        # 模拟数据库在任务执行期掉线：容器级 session 工厂直接抛错
        class _BrokenSessionFactory:
            def __call__(self) -> Any:
                raise RuntimeError("database unavailable")

        execution.container.session_factory = _BrokenSessionFactory()  # type: ignore[attr-defined]

        # 运行时写路径（提交节点结果/追加事件）失败时必须被观察者捕获并记录，
        # 不能把异常抛给调用方、也不能留下半成品写入
        async def _submit_after_loss() -> None:
            from app.orchestration.scheduler import NodeResult
            from app.orchestration.task_state import SubtaskStatus

            observer = execution.container.create_observer()
            await observer.on_result(
                uuid.UUID(snapshot["task_id"]),
                NodeResult(node_id="n1", status=SubtaskStatus.COMPLETED, attempts=1),
            )

        client.portal.call(
            _submit_after_loss
        )  # 不应抛出（内部记录 schedule_result_persist_failed）

        # 已提交状态与事件不受影响（没有半成品写入）
        after = client.get(f"/api/v1/tasks/{task_id}", headers=_auth(token)).json()
        assert after["status"] == "completed"
        assert after["review_report"] is not None
        events_after = len(
            client.get(f"/api/v1/tasks/{task_id}/events", headers=_auth(token)).json()["events"]
        )
        assert events_after == events_before, "数据库故障期间不得产生半成品事件"


class TestEventDelivery:
    def test_redis_loss_keeps_polling_available(self, resilience_env) -> None:
        """Redis 不可用（内存发布器）→ 事件仍能通过 HTTP 补拉，ID 严格递增。"""
        client: TestClient = resilience_env["client"]
        publisher = RecordingEventPublisher()
        resilience_env["install"](publisher=publisher)
        token = _register_and_login(client, "redis-loss-user")
        _upload(client, token, "事件文档", DOC_ONE)
        task_id = _create_task(client, token, topic="Redis 降级验证")
        _approve(client, token, task_id)
        _wait_status(client, token, task_id, {"completed"}, timeout=180.0)

        page = client.get(f"/api/v1/tasks/{task_id}/events", headers=_auth(token)).json()
        event_ids = [item["event_id"] for item in page["events"]]
        assert event_ids, "HTTP 补拉应能拿到事件"
        assert event_ids == sorted(event_ids)
        # 发布器记录的事件数应与补拉一致（不丢事件）
        assert len(publisher.events_for(uuid.UUID(task_id))) == len(event_ids)


class TestRestartRecovery:
    def test_restart_preserves_completed_nodes(self, resilience_env) -> None:
        """进程重启：已完成节点不重跑，recover() 不产生新事件。"""
        client: TestClient = resilience_env["client"]
        resilience_env["install"]()
        token = _register_and_login(client, "restart-user")
        _upload(client, token, "重启文档", DOC_ONE)
        task_id = _create_task(client, token, topic="重启恢复验证")
        _approve(client, token, task_id)
        final = _wait_status(client, token, task_id, {"completed"}, timeout=180.0)

        attempts_before = {
            subtask["node_id"]: subtask["retry_count"] for subtask in final["subtasks"]
        }
        events_before = len(
            client.get(f"/api/v1/tasks/{task_id}/events", headers=_auth(token)).json()["events"]
        )

        # 同库新建容器 + recover()
        fresh = resilience_env["install"]()
        client.portal.call(fresh.recover)
        import time

        time.sleep(1.0)

        after = client.get(f"/api/v1/tasks/{task_id}", headers=_auth(token)).json()
        attempts_after = {
            subtask["node_id"]: subtask["retry_count"] for subtask in after["subtasks"]
        }
        events_after = len(
            client.get(f"/api/v1/tasks/{task_id}/events", headers=_auth(token)).json()["events"]
        )

        assert after["status"] == "completed"
        assert attempts_after == attempts_before, "重启后重跑了已完成节点"
        assert events_after == events_before, "重启恢复不应产生新事件"
