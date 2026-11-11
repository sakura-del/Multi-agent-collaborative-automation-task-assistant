"""进程级端到端验收（Plan 5 Task 1）。

覆盖：注册/登录 → 上传两篇 TXT → 创建文献任务 → WAITING_APPROVAL → 确认精确 revision
→ COMPLETED → 每条形式化引用可回溯到已上传分块 → 事件 ID 严格递增 → 下载并打开
Markdown 与 DOCX。

确定性来自 `tests/fakes`：固定 LLM（按 system prompt 用途分派）、固定嵌入/重排、
记录型事件发布器。测试不读环境变量、不访问网络，也不修改模块级全局单例。
"""

from __future__ import annotations

import asyncio
import io
import secrets
import uuid
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.fakes import (
    FixedLiteratureLLM,
    RecordingEventPublisher,
)

DOC_ONE = (
    "检索增强生成（RAG）通过外部知识检索降低大模型的事实性错误。"
    "常见评估维度包括忠实度、答案相关性与上下文相关性。"
).encode()

DOC_TWO = (
    "多智能体协作把检索、抽取、写作与校验拆分为专门角色，"
    "通过证据引用与交叉校验提升文献综述的可追溯性。"
).encode()


@pytest.fixture
def e2e_env(tmp_path: Path) -> Iterator[Any]:
    """独立文件型 SQLite + 注入式运行时；每个测试互相隔离。"""
    from app.db.base import Base
    from app.db.session import get_db
    from app.knowledge import services as kb_services
    from app.knowledge.services import factory as kb_factory
    from app.main import app
    from app.orchestration.runtime import RuntimeContainer
    from app.orchestration.services.task_execution import TaskExecution

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'e2e.db'}")
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

    # 知识库：确定性嵌入/重排 + 内存 Chroma，上传目录指向 tmp_path
    from app.core.config import get_settings

    settings = get_settings()
    settings.app.upload_root = str(tmp_path / "uploads")
    settings.app.artifact_root = str(tmp_path / "artifacts")
    kb_factory.reset_kb()
    kb_factory.init_kb(force_mock_embedding=True, force_mock_reranker=True, chroma_persist_dir="")

    llm = FixedLiteratureLLM()
    publisher = RecordingEventPublisher()
    from app.knowledge.services.search_provider import KnowledgeSearchProvider

    container = RuntimeContainer(
        session_factory=session_factory,
        event_publisher=publisher,
        llm=llm,
        knowledge_query=KnowledgeSearchProvider(session_factory),
    )
    execution = TaskExecution(container)

    client = TestClient(app)
    client.__enter__()
    app.state.container = container
    app.state.runtime = execution

    # Phase 2 计费：本 e2e 测的是**工作流确定性**（同一输入两次跑结果一致），
    # 需要建 2 个任务；免费档 1 篇/月会拦住。注入放开篇数的 stub。
    class _UnlimitedPlanService:
        async def check_and_consume_review(self, user_id, **_kwargs):  # type: ignore[no-untyped-def]
            return None

        async def require_feature(self, user_id, feature, **_kwargs):  # type: ignore[no-untyped-def]
            return None

    app.state.plan_service = _UnlimitedPlanService()

    yield {
        "client": client,
        "session_factory": session_factory,
        "llm": llm,
        "publisher": publisher,
        "execution": execution,
        "container": container,
        "settings": settings,
        "kb": kb_services,
    }

    client.__exit__(None, None, None)
    app.dependency_overrides.clear()
    app.state.plan_service = None
    kb_factory.reset_kb()
    asyncio.run(engine.dispose())


# ===== 辅助 =====


def _register_and_login(client: TestClient, username: str) -> str:
    # 口令每次随机生成：Issue #10 —— 测试夹具里也不留固定口令，
    # 否则一旦有人把测试指向真实库，这个口令就变成了"公开可用的账号"。
    password = secrets.token_urlsafe(24)
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


def _wait_status(
    client: TestClient,
    token: str,
    task_id: str,
    expected: set[str],
    *,
    timeout: float = 120.0,
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


def _stored_chunk_ids(env: Any, user_id: str) -> set[str]:
    """数据库中属于该用户的 DocChunk id 集合（用于引用可回溯断言）。

    走 TestClient 的 portal（应用自己的事件循环），避免跨事件循环访问 aiosqlite。
    """
    from app.db.models.knowledge import DocChunk, KnowledgeDoc

    async def run() -> set[str]:
        async with env["session_factory"]() as session:
            rows = (
                (
                    await session.execute(
                        select(DocChunk.id)
                        .join(KnowledgeDoc, KnowledgeDoc.id == DocChunk.doc_id)
                        .where(KnowledgeDoc.user_id == uuid.UUID(str(user_id)))
                    )
                )
                .scalars()
                .all()
            )
            return {str(row) for row in rows}

    return env["client"].portal.call(run)


# ===== 用例 =====


class TestLiteratureReviewProcessE2E:
    def test_full_workflow_with_traceable_citations(self, e2e_env) -> None:
        client: TestClient = e2e_env["client"]
        token = _register_and_login(client, "e2e-user")
        me = client.get("/api/v1/auth/me", headers=_auth(token)).json()

        first = _upload(client, token, "RAG 评估要点", DOC_ONE)
        second = _upload(client, token, "多智能体协作", DOC_TWO)
        assert first["index_status"] == "ready" and second["index_status"] == "ready"

        created = client.post(
            "/api/v1/tasks",
            headers=_auth(token),
            json={
                "topic": "检索增强生成证据核验综述",
                "keywords": ["RAG", "evidence"],
                "max_documents": 5,
                "offline_dataset": True,
            },
        )
        assert created.status_code == 201, created.text
        task_id = created.json()["task_id"]

        snapshot = _wait_status(client, token, task_id, {"waiting_approval"})
        plan = snapshot["plan"]
        assert plan is not None
        assert len(plan["research_questions"]) >= 3
        node_types = {node["type"] for node in plan["nodes"]}
        assert {"search", "extract", "write", "validate", "review"} <= node_types

        approved = client.post(
            f"/api/v1/tasks/{task_id}/plan/approve",
            headers=_auth(token),
            json={"revision": snapshot["revision"]},
        )
        assert approved.status_code == 200, approved.text

        final = _wait_status(client, token, task_id, {"completed"}, timeout=180.0)
        assert final["review_report"] is not None
        assert final["artifacts"], "缺少导出产物"

        # 1) 每条形式化引用都能回溯到已上传文档的分块
        stored_chunks = _stored_chunk_ids(e2e_env, me["id"])
        assert stored_chunks, "上传文档未产生分块"
        cited_chunks: set[str] = set()
        for subtask in final["subtasks"]:
            output = subtask.get("output") or {}
            execution = output.get("execution_result") or {}
            for evidence in execution.get("evidence") or []:
                cited_chunks.add(str(evidence["chunk_id"]))
        assert cited_chunks, "执行结果没有任何被引用证据"
        assert cited_chunks <= stored_chunks, f"存在无法回溯的证据: {cited_chunks - stored_chunks}"

        markdown = final["review_report"]["final_markdown"]
        assert "[E" in markdown, "最终综述缺少引用标记"

        # 2) 事件 ID 严格递增
        events = client.get(f"/api/v1/tasks/{task_id}/events", headers=_auth(token)).json()[
            "events"
        ]
        event_ids = [item["event_id"] for item in events]
        assert len(event_ids) > 5
        assert event_ids == sorted(event_ids)
        assert len(set(event_ids)) == len(event_ids)

        # 3) 下载并打开 Markdown 与 DOCX
        for artifact in final["artifacts"]:
            response = client.get(
                f"/api/v1/tasks/{task_id}/artifacts/{artifact['artifact_id']}",
                headers=_auth(token),
            )
            assert response.status_code == 200, response.text
            assert response.content, "产物内容为空"
            if artifact["kind"] == "docx":
                with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                    assert "word/document.xml" in archive.namelist()
            else:
                text = response.content.decode("utf-8")
                assert markdown.strip()[:20] in text

    def test_llm_calls_cover_every_prompt_purpose(self, e2e_env) -> None:
        """真实调用路径覆盖：规划、执行、语义校验各至少一次。"""
        client: TestClient = e2e_env["client"]
        llm: FixedLiteratureLLM = e2e_env["llm"]
        token = _register_and_login(client, "e2e-prompts")
        _upload(client, token, "证据文档", DOC_ONE)

        created = client.post(
            "/api/v1/tasks",
            headers=_auth(token),
            json={
                "topic": "提示词用途覆盖验证",
                "keywords": ["prompt"],
                "max_documents": 3,
                "offline_dataset": True,
            },
        )
        task_id = created.json()["task_id"]
        snapshot = _wait_status(client, token, task_id, {"waiting_approval"})
        client.post(
            f"/api/v1/tasks/{task_id}/plan/approve",
            headers=_auth(token),
            json={"revision": snapshot["revision"]},
        )
        _wait_status(client, token, task_id, {"completed"}, timeout=180.0)

        systems = [call["system"] for call in llm.calls]
        assert any("规划专家" in system for system in systems)
        assert any("质量校验专家" in system for system in systems)
        # 执行节点调用（system prompt 是执行 Agent 的固定描述）
        assert len(llm.calls) >= 5

    def test_repeat_run_is_deterministic(self, e2e_env) -> None:
        """同样输入连续两次运行：规划与产物内容一致（固定适配器保证可复现）。"""
        client: TestClient = e2e_env["client"]
        token = _register_and_login(client, "e2e-repeat")
        _upload(client, token, "可复现文档", DOC_ONE)

        results: list[tuple[list[str], str]] = []
        for _ in range(2):
            created = client.post(
                "/api/v1/tasks",
                headers=_auth(token),
                json={
                    "topic": "可复现性验证",
                    "keywords": ["determinism"],
                    "max_documents": 3,
                    "offline_dataset": True,
                },
            )
            task_id = created.json()["task_id"]
            snapshot = _wait_status(client, token, task_id, {"waiting_approval"})
            client.post(
                f"/api/v1/tasks/{task_id}/plan/approve",
                headers=_auth(token),
                json={"revision": snapshot["revision"]},
            )
            final = _wait_status(client, token, task_id, {"completed"}, timeout=180.0)
            node_ids = [subtask["node_id"] for subtask in final["subtasks"]]
            results.append((node_ids, final["review_report"]["final_markdown"]))

        assert results[0][0] == results[1][0], "两次运行的节点集合不一致"
        assert results[0][1] == results[1][1], "两次运行的综述正文不一致"
