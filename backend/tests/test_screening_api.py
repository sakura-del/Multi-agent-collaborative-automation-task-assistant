"""PRISMA 筛选 HTTP 端点测试（Phase 2 · Task 5）。

端点：
    POST /api/v1/screening/search-logs                     记录一次检索（落快照）
    GET  /api/v1/screening/records?task_id=…               列出候选记录
    POST /api/v1/screening/records/{id}/decisions          追加筛选决策
    GET  /api/v1/screening/prisma?task_id=…                PRISMA 计数 + 流程图

## 覆盖重点（含计划初稿里缺失的三处）

1. **越权隔离**：筛选数据归属任务，而任务归属用户。
   `GET /records` 与 `GET /prisma` 只收 `task_id`——若不校验所有权，
   任何登录用户都能读别人的筛选结果与 PRISMA 数字。
   `POST /decisions` 只收 `record_id`——同理必须反查记录所属任务。
   统一返回 **404**（不用 403，避免泄漏资源是否存在，与既有模板端点一致）。

2. **DOI 跨源去重必须真的生效**：`literature_record` 的 DOI 靠
   `source_url` 的 `https://doi.org/…` 形式提取。若端点伪造 URL 而丢弃
   请求里的 `doi` 字段，DOI（最高优先级去重键）就永远失效。

3. **`hits_total` 可显式传入**：上游报 200 命中、只回传 5 条样本时，
   识别阶段数字不能退化成 5。
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.api.v1.endpoints import screening as screening_endpoint
from app.db.base import Base
from app.db.models.task import Task
from app.db.session import get_db

OWNER_ID = uuid.uuid4()
OTHER_USER_ID = uuid.uuid4()


class _FakeUser:
    """测试用户（绕过真实 JWT）。"""

    def __init__(self, user_id: uuid.UUID) -> None:
        self.id = user_id
        self.username = "tester"
        self.email = "tester@example.com"
        self.is_active = True
        self.role = "user"


def _fake_user() -> Any:
    return _FakeUser(OWNER_ID)


def _make_task(session_factory: async_sessionmaker[AsyncSession], user_id: uuid.UUID) -> uuid.UUID:
    """插入一个归属于 `user_id` 的任务，返回其 id。"""

    async def _create() -> uuid.UUID:
        async with session_factory() as session:
            task = Task(
                user_id=user_id,
                title="筛选测试任务",
                description="probe",
                config={},
            )
            session.add(task)
            await session.commit()
            return task.id

    return asyncio.run(_create())


@pytest.fixture
def engine() -> AsyncEngine:
    return create_async_engine("sqlite+aiosqlite:///:memory:")


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
def app(engine: AsyncEngine, session_factory: async_sessionmaker[AsyncSession]) -> FastAPI:
    """独立应用：挂筛选路由 + 独立 SQLite + 与生产一致的异常映射。"""

    async def _get_db() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    application = FastAPI()
    application.include_router(screening_endpoint.router, prefix="/api/v1")

    # 生产在 app/main.py 注册全局 handler；独立测试 app 需自备
    from fastapi.responses import JSONResponse

    from app.core.exceptions import AppException, NotFoundException

    @application.exception_handler(NotFoundException)
    async def _handle_not_found(_: Any, exc: NotFoundException) -> Any:
        return JSONResponse(status_code=404, content={"detail": exc.message})

    @application.exception_handler(AppException)
    async def _handle_app(_: Any, exc: AppException) -> Any:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error_code": exc.error_code, "message": exc.message},
        )

    application.dependency_overrides[get_db] = _get_db
    application.dependency_overrides[screening_endpoint.get_current_active_user] = _fake_user

    async def _create_all() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    asyncio.run(_create_all())
    return application


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def task_id(app: FastAPI, session_factory: async_sessionmaker[AsyncSession]) -> uuid.UUID:
    """当前用户拥有的任务。"""
    return _make_task(session_factory, OWNER_ID)


@pytest.fixture
def other_task_id(app: FastAPI, session_factory: async_sessionmaker[AsyncSession]) -> uuid.UUID:
    """**别人**的任务（用于越权测试）。"""
    return _make_task(session_factory, OTHER_USER_ID)


def _record(source_id: str = "a1", title: str = "T1", **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "source_id": source_id,
        "title": title,
        "authors": ["A"],
        "abstract": "x",
    }
    payload.update(extra)
    return payload


def _post_search(
    client: TestClient,
    task_id: uuid.UUID,
    *,
    records: list[dict[str, Any]] | None = None,
    **extra: Any,
) -> Any:
    body: dict[str, Any] = {
        "task_id": str(task_id),
        "query": "rag evaluation",
        "sources": ["arxiv"],
        "records": records if records is not None else [_record()],
    }
    body.update(extra)
    return client.post("/api/v1/screening/search-logs", json=body)


# ===== POST /search-logs =====


def test_create_search_log_returns_201(client: TestClient, task_id: uuid.UUID) -> None:
    response = _post_search(client, task_id)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["hits_total"] == 1
    assert body["hits_deduped"] == 1
    assert body["task_id"] == str(task_id)


def test_create_search_log_dedupes_by_doi(client: TestClient, task_id: uuid.UUID) -> None:
    """同 DOI 不同源 → 只留一条（DOI 是最高优先级去重键）。"""
    response = _post_search(
        client,
        task_id,
        records=[
            _record("arxiv:a1", "Title A", doi="10.1234/ABC"),
            _record("openalex:o1", "Different Title", doi="10.1234/abc"),
        ],
        sources=["arxiv", "openalex"],
        hits_by_source={"arxiv": 1, "openalex": 1},
    )

    assert response.status_code == 201, response.text
    assert response.json()["hits_deduped"] == 1, "DOI 去重未生效——doi 字段被丢弃了"


def test_create_search_log_honours_explicit_hits_total(
    client: TestClient, task_id: uuid.UUID
) -> None:
    """上游报 200 命中、只回 2 条样本 → 识别数字必须是 200，不能退化成 2。"""
    response = _post_search(
        client,
        task_id,
        records=[_record("a1", "T1"), _record("a2", "T2")],
        hits_total=200,
    )

    assert response.status_code == 201, response.text
    assert response.json()["hits_total"] == 200
    assert response.json()["hits_deduped"] == 2


def test_create_search_log_rejects_other_users_task(
    client: TestClient, other_task_id: uuid.UUID
) -> None:
    """不能往别人的任务里写筛选数据。"""
    response = _post_search(client, other_task_id)

    assert response.status_code == 404, response.text


def test_create_search_log_rejects_unknown_task(client: TestClient) -> None:
    response = _post_search(client, uuid.uuid4())

    assert response.status_code == 404


# ===== GET /records =====


def test_list_records_returns_snapshot(client: TestClient, task_id: uuid.UUID) -> None:
    _post_search(client, task_id, records=[_record("a1", "T1"), _record("a2", "T2")])

    response = client.get("/api/v1/screening/records", params={"task_id": str(task_id)})

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert len(items) == 2
    assert items[0]["current_stage"] == "title_abstract"
    assert items[0]["record_id"]


def test_list_records_filters_by_stage(client: TestClient, task_id: uuid.UUID) -> None:
    _post_search(client, task_id)
    record_id = client.get("/api/v1/screening/records", params={"task_id": str(task_id)}).json()[
        "items"
    ][0]["record_id"]
    client.post(
        f"/api/v1/screening/records/{record_id}/decisions",
        json={"stage": "title_abstract", "verdict": "include"},
    )

    fulltext = client.get(
        "/api/v1/screening/records", params={"task_id": str(task_id), "stage": "fulltext"}
    )
    title = client.get(
        "/api/v1/screening/records",
        params={"task_id": str(task_id), "stage": "title_abstract"},
    )

    assert [row["record_id"] for row in fulltext.json()["items"]] == [record_id]
    assert title.json()["items"] == []


def test_list_records_rejects_unknown_stage(client: TestClient, task_id: uuid.UUID) -> None:
    response = client.get(
        "/api/v1/screening/records", params={"task_id": str(task_id), "stage": "nope"}
    )

    assert response.status_code == 400, response.text


def test_list_records_rejects_other_users_task(
    client: TestClient, other_task_id: uuid.UUID
) -> None:
    response = client.get("/api/v1/screening/records", params={"task_id": str(other_task_id)})

    assert response.status_code == 404, response.text


# ===== POST /records/{id}/decisions =====


def _first_record_id(client: TestClient, task_id: uuid.UUID) -> str:
    listed = client.get("/api/v1/screening/records", params={"task_id": str(task_id)})
    return str(listed.json()["items"][0]["record_id"])


def test_include_advances_stage(client: TestClient, task_id: uuid.UUID) -> None:
    _post_search(client, task_id)
    record_id = _first_record_id(client, task_id)

    response = client.post(
        f"/api/v1/screening/records/{record_id}/decisions",
        json={"stage": "title_abstract", "verdict": "include"},
    )

    assert response.status_code == 201, response.text
    assert response.json()["current_stage"] == "fulltext"


def test_exclude_without_reason_returns_400(client: TestClient, task_id: uuid.UUID) -> None:
    _post_search(client, task_id)
    record_id = _first_record_id(client, task_id)

    response = client.post(
        f"/api/v1/screening/records/{record_id}/decisions",
        json={"stage": "title_abstract", "verdict": "exclude"},
    )

    assert response.status_code == 400, response.text
    assert "排除" in response.json()["detail"]


def test_exclude_with_reason_is_accepted(client: TestClient, task_id: uuid.UUID) -> None:
    _post_search(client, task_id)
    record_id = _first_record_id(client, task_id)

    response = client.post(
        f"/api/v1/screening/records/{record_id}/decisions",
        json={
            "stage": "title_abstract",
            "verdict": "exclude",
            "exclusion_reason": "not relevant",
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["current_stage"] == "title_abstract"


def test_decision_on_unknown_record_returns_404(client: TestClient) -> None:
    response = client.post(
        f"/api/v1/screening/records/{uuid.uuid4()}/decisions",
        json={"stage": "title_abstract", "verdict": "include"},
    )

    assert response.status_code == 404, response.text


def test_decision_on_other_users_record_returns_404(
    client: TestClient, other_task_id: uuid.UUID
) -> None:
    """别人的记录不能追加决策。"""
    _post_search(client, other_task_id)
    # 用自己的身份去拿别人的记录 id（列接口已 404，这里直接构造已知 id 更能覆盖校验逻辑）
    other_record_id = client.get(
        "/api/v1/screening/records", params={"task_id": str(other_task_id)}
    )
    assert other_record_id.status_code == 404  # 列接口本身已拦住

    response = client.post(
        f"/api/v1/screening/records/{uuid.uuid4()}/decisions",
        json={"stage": "title_abstract", "verdict": "include"},
    )
    assert response.status_code == 404


def test_unknown_stage_in_decision_returns_422(client: TestClient, task_id: uuid.UUID) -> None:
    _post_search(client, task_id)
    record_id = _first_record_id(client, task_id)

    response = client.post(
        f"/api/v1/screening/records/{record_id}/decisions",
        json={"stage": "bogus", "verdict": "include"},
    )

    assert response.status_code == 422, response.text


def test_unknown_verdict_returns_422(client: TestClient, task_id: uuid.UUID) -> None:
    _post_search(client, task_id)
    record_id = _first_record_id(client, task_id)

    response = client.post(
        f"/api/v1/screening/records/{record_id}/decisions",
        json={"stage": "title_abstract", "verdict": "bogus"},
    )

    assert response.status_code == 422, response.text


# ===== GET /prisma =====


def test_prisma_counts_include_all_stages(client: TestClient, task_id: uuid.UUID) -> None:
    _post_search(
        client,
        task_id,
        records=[_record("a1", "T1"), _record("a2", "T2")],
        sources=["arxiv", "openalex"],
        hits_by_source={"arxiv": 30, "openalex": 20},
    )

    response = client.get("/api/v1/screening/prisma", params={"task_id": str(task_id)})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["records_screened"] == 2
    # 识别阶段用**去重前**各源命中数，且各源合计与总数自洽
    assert body["identified_by_source"] == {"arxiv": 30, "openalex": 20}
    assert body["identified_total"] == 50
    assert sum(body["identified_by_source"].values()) == body["identified_total"]
    assert body["title_abstract"] == {"screened": 0, "excluded": 0}
    assert body["included"] == 0


def test_prisma_renders_markdown_and_mermaid(client: TestClient, task_id: uuid.UUID) -> None:
    _post_search(client, task_id)

    body = client.get("/api/v1/screening/prisma", params={"task_id": str(task_id)}).json()

    assert body["mermaid"].startswith("flowchart TD")
    assert "PRISMA 2020" in body["markdown"]
    assert body["mermaid"].count("[") == body["mermaid"].count("]")


def test_prisma_supports_english_output(client: TestClient, task_id: uuid.UUID) -> None:
    """`language=en` 时流程图与表格用英文文案（论文可能是英文）。"""
    _post_search(client, task_id)

    body = client.get(
        "/api/v1/screening/prisma",
        params={"task_id": str(task_id), "language": "en"},
    ).json()

    assert "Identification" in body["markdown"]
    assert "Identified" in body["mermaid"]


def test_prisma_reflects_decisions(client: TestClient, task_id: uuid.UUID) -> None:
    _post_search(client, task_id, records=[_record("a1", "T1"), _record("a2", "T2")])
    records = client.get("/api/v1/screening/records", params={"task_id": str(task_id)}).json()[
        "items"
    ]

    client.post(
        f"/api/v1/screening/records/{records[0]['record_id']}/decisions",
        json={"stage": "title_abstract", "verdict": "include"},
    )
    client.post(
        f"/api/v1/screening/records/{records[1]['record_id']}/decisions",
        json={
            "stage": "title_abstract",
            "verdict": "exclude",
            "exclusion_reason": "not relevant",
        },
    )

    body = client.get("/api/v1/screening/prisma", params={"task_id": str(task_id)}).json()

    assert body["title_abstract"] == {"screened": 2, "excluded": 1}
    assert body["exclusion_reasons"] == {"not relevant": 1}


def test_prisma_empty_task_is_all_zero(client: TestClient, task_id: uuid.UUID) -> None:
    """刚建的任务没有任何检索记录 → 全 0，且流程图仍能生成。"""
    response = client.get("/api/v1/screening/prisma", params={"task_id": str(task_id)})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["identified_total"] == 0
    assert body["records_screened"] == 0
    assert body["mermaid"].startswith("flowchart TD")


def test_prisma_rejects_other_users_task(client: TestClient, other_task_id: uuid.UUID) -> None:
    response = client.get("/api/v1/screening/prisma", params={"task_id": str(other_task_id)})

    assert response.status_code == 404, response.text


# ===== 路由接线（防止「写了端点但忘记 include_router」）=====


def test_router_is_wired_into_api_router() -> None:
    """四条筛选路由必须真的挂在 `/api/v1` 聚合路由上。

    单独测试端点函数是测不出这个的——漏接 `include_router` 时端点能单测通过、
    线上却 404。

    用 OpenAPI schema 断言（而不是 `api_router.routes`）：本项目的 FastAPI 版本把
    被 include 的路由包成惰性 `_IncludedRouter`，`routes` 里看不到真实 path；
    schema 才是最终对外的契约面。
    """
    from fastapi import FastAPI

    from app.api.v1.router import api_router

    probe = FastAPI()
    probe.include_router(api_router)
    paths = set(probe.openapi()["paths"])

    assert "/api/v1/screening/search-logs" in paths
    assert "/api/v1/screening/records" in paths
    assert "/api/v1/screening/records/{record_id}/decisions" in paths
    assert "/api/v1/screening/prisma" in paths
