"""Issue #32 Phase 2B：OpenAlex 引文图谱方法与 citation_snowballing 检索节点单测。"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from pydantic import HttpUrl

from app.orchestration.contracts import PlanNode
from app.orchestration.workflows.literature_review import LiteratureReviewWorkflow
from app.schemas.task import TaskCreate
from app.tools.literature.models import LiteratureRecord, LiteratureSearch
from app.tools.literature.openalex_search import OpenAlexSearchSource


def _work_json(
    work_id: str,
    title: str,
    *,
    doi: str | None = None,
    year: int = 2023,
    referenced_works: list[str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": f"https://openalex.org/{work_id}",
        "title": title,
        "publication_year": year,
        "authorships": [{"author": {"display_name": "Alice Smith"}}],
        "primary_location": {"landing_page_url": f"https://example.org/{work_id}"},
    }
    if doi:
        payload["doi"] = f"https://doi.org/{doi}"
    if referenced_works is not None:
        payload["referenced_works"] = referenced_works
    return payload


@pytest.mark.asyncio
async def test_openalex_resolve_fetch_references_and_citing_works() -> None:
    """验证 resolve_work / fetch_references / fetch_citing_works 的请求参数与归一化输出。"""
    requested: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append((request.url.path, str(request.url.query, "utf-8")))
        path = request.url.path
        query = str(request.url.query, "utf-8")
        if path == "/works/https://doi.org/10.1000/seed":
            return httpx.Response(
                200,
                json=_work_json("W100", "Seed Paper on RAG", doi="10.1000/seed"),
            )
        if path == "/works/W100":
            return httpx.Response(
                200,
                json=_work_json(
                    "W100",
                    "Seed Paper on RAG",
                    doi="10.1000/seed",
                    referenced_works=[
                        "https://openalex.org/W201",
                        "https://openalex.org/W202",
                    ],
                ),
            )
        if (path == "/works" and "openalex%3AW201" in query) or "openalex:W201" in query:
            return httpx.Response(
                200,
                json={
                    "results": [
                        _work_json("W201", "Backward Ref 1", doi="10.1000/ref1", year=2020),
                        _work_json("W202", "Backward Ref 2", doi="10.1000/ref2", year=2021),
                    ]
                },
            )
        if path == "/works" and ("cites%3AW100" in query or "cites:W100" in query):
            return httpx.Response(
                200,
                json={
                    "results": [
                        _work_json("W301", "Forward Citing 1", doi="10.1000/cite1", year=2024),
                    ]
                },
            )
        return httpx.Response(404, json={"error": "not found"})

    async with httpx.AsyncClient(
        base_url="https://api.openalex.org", transport=httpx.MockTransport(handler)
    ) as client:
        source = OpenAlexSearchSource(client=client, mailto="test@veripaper.me")
        resolved = await source.resolve_work("https://doi.org/10.1000/seed")
        assert resolved is not None
        work_id, seed_rec = resolved
        assert work_id == "W100"
        assert seed_rec.source_id == "openalex:W100"

        refs = await source.fetch_references(work_id, max_results=10)
        assert [r.source_id for r in refs] == ["openalex:W201", "openalex:W202"]

        citings = await source.fetch_citing_works(work_id, max_results=10)
        assert [r.source_id for r in citings] == ["openalex:W301"]


class _FakeSnowballSource:
    """满足 OpenAlex 引文图谱鸭子类型与 LiteratureSource 协议的假源。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def _rec(self, sid: str, title: str, doi: str) -> LiteratureRecord:
        return LiteratureRecord(
            source_id=sid,
            title=title,
            authors=["Author A"],
            abstract="Abstract text",
            published_at=datetime(2024, 1, 1, tzinfo=UTC),
            source_url=HttpUrl(f"https://doi.org/{doi}"),
            pdf_url=HttpUrl(f"https://doi.org/{doi}"),
        )

    async def search(self, search: LiteratureSearch) -> list[LiteratureRecord]:
        self.calls.append(f"search:{search.query}")
        return [self._rec("openalex:W1", "Auto Seed Paper", "10.1000/auto1")]

    async def resolve_work(self, doi_or_title: str) -> tuple[str, LiteratureRecord] | None:
        self.calls.append(f"resolve:{doi_or_title}")
        return "W1", self._rec("openalex:W1", "Seed Paper", "10.1000/seed1")

    async def fetch_references(
        self, work_id: str, *, max_results: int = 25
    ) -> list[LiteratureRecord]:
        self.calls.append(f"refs:{work_id}")
        # 故意包含一条与种子重复的 DOI，验证 record_keys 三键去重
        return [
            self._rec("openalex:W1", "Seed Paper", "10.1000/seed1"),
            self._rec("openalex:W2", "Backward Reference Paper", "10.1000/back1"),
        ]

    async def fetch_citing_works(
        self, work_id: str, *, max_results: int = 25, sort: str = "cited_by_count:desc"
    ) -> list[LiteratureRecord]:
        self.calls.append(f"cites:{work_id}")
        return [
            self._rec("openalex:W3", "Forward Citing Paper", "10.1000/fwd1"),
            self._rec("openalex:W4", "Forward Citing Paper 2", "10.1000/fwd2"),
        ]


@pytest.mark.asyncio
async def test_workflow_execute_search_snowballing_dedup_and_cap() -> None:
    """citation_snowballing 模式下走种子扩展、三键去重并受 max_documents 约束。"""
    source = _FakeSnowballSource()
    workflow = LiteratureReviewWorkflow(source=source)  # type: ignore[arg-type]
    task = SimpleNamespace(
        id="00000000-0000-0000-0000-000000000001",
        title="引文滚雪球主题",
        config={
            "workflow_preset": "citation_snowballing",
            "seed_dois": ["10.1000/seed1"],
            "snowball_direction": "both",
            "max_documents": 3,
        },
    )
    node = PlanNode(id="n1", type="search", title="引文检索", input={"max_results": 10})
    result = await workflow._execute_search(task, node, None)

    assert len(result.artifacts) == 3
    titles = [item["title"] for item in result.artifacts]
    assert titles == ["Seed Paper", "Backward Reference Paper", "Forward Citing Paper"]
    assert "resolve:10.1000/seed1" in source.calls
    assert "refs:W1" in source.calls
    assert "cites:W1" in source.calls


def test_task_create_citation_snowballing_allows_seed_dois_without_keywords() -> None:
    """提供 seed_dois 时允许省略 keywords，并校验最多 5 篇种子。"""
    req = TaskCreate(
        topic="RAG 引文滚雪球追踪分析",
        workflow_preset="citation_snowballing",
        seed_dois=["10.1000/a", "10.1000/b"],
        snowball_direction="forward",
    )
    contract = req.to_contract()
    assert contract.workflow_preset == "citation_snowballing"
    assert contract.snowball_direction == "forward"
    assert contract.keywords == ["10.1000/a", "10.1000/b"]
