"""自定义论文抽取表（Issue #36）后端单元测试：模型/路由、按列抽取、幻觉拦截与 CSV 安全导出。"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.v1.endpoints import extractions as extraction_endpoints
from app.db.base import Base
from app.db.models.extraction import ExtractionCell, ExtractionTable
from app.db.models.knowledge import DocChunk, KnowledgeDoc
from app.db.models.screening import DecisionStage, ScreeningRecord, SearchLog
from app.db.models.task import Task
from app.db.models.user import User
from app.db.session import get_db
from app.knowledge.services.table_extractor import (
    TableExtractor,
    _safe_csv_cell,
    export_table_csv,
    format_locator_text,
)


class _ScriptedExtractionLLM:
    """按脚本返回结构化 JSON 的假 LLM，同时记录调用次数与入参。"""

    def __init__(self, handler: Any) -> None:
        self._handler = handler
        self.calls: list[dict[str, Any]] = []

    async def chat(self, messages: list[Any], **kwargs: Any) -> Any:
        payload = json.loads(messages[-1].content)
        self.calls.append(payload)
        result = self._handler(payload)
        if isinstance(result, Exception):
            raise result
        content = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        return SimpleNamespace(content=content)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _register_and_login(client: TestClient, username: str) -> tuple[str, uuid.UUID]:
    reg = client.post(
        "/api/v1/auth/register",
        json={
            "username": username,
            "email": f"{username}@example.com",
            "password": "Password123!",
        },
    )
    assert reg.status_code == 201, reg.text
    login = client.post(
        "/api/v1/auth/login",
        json={"account": username, "password": "Password123!"},
    )
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    me = client.get("/api/v1/auth/me", headers=_auth(token))
    assert me.status_code == 200
    return token, uuid.UUID(me.json()["id"])


@pytest.fixture
def client(tmp_path) -> Iterator[TestClient]:
    """独立文件型 SQLite + FastAPI TestClient。"""
    from app.main import app

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'extraction_test.db'}", echo=False
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    state: dict[str, bool] = {"created": False}

    async def override_get_db() -> AsyncSession:
        if not state["created"]:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            state["created"] = True
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    test_client = TestClient(app)
    test_client.__enter__()
    test_client.engine = engine  # type: ignore[attr-defined]
    test_client.session_factory = session_factory  # type: ignore[attr-defined]
    yield test_client
    test_client.__exit__(None, None, None)
    app.dependency_overrides.clear()
    asyncio.run(engine.dispose())


async def _seed_knowledge_doc(
    session_factory: async_sessionmaker[AsyncSession],
    user_id: uuid.UUID,
    *,
    title: str = "多智能体 RAG 评测研究",
    chunks: list[tuple[str, dict[str, Any] | None, dict[str, Any] | None]] | None = None,
) -> uuid.UUID:
    doc_id = uuid.uuid4()
    async with session_factory() as session:
        doc = KnowledgeDoc(
            id=doc_id,
            user_id=user_id,
            title=title,
            source="upload",
            doc_type="pdf",
            file_path=f"/tmp/{doc_id}.pdf",
            index_status="ready",
            chunk_count=len(chunks or []),
            meta_info={"year": 2026},
        )
        session.add(doc)
        await session.flush()
        for idx, (content, pos, meta) in enumerate(
            chunks
            or [
                (
                    "3.2 实验设置：本研究纳入 N=320 名受试者，如 Table 2 所示准确率提升至 94.2%。",
                    {"start_offset": 0, "end_offset": 45, "page_start": 5, "page_end": 6},
                    {"section_title": "3.2 实验设置", "table_ref": "Table 2"},
                )
            ]
        ):
            session.add(
                DocChunk(
                    doc_id=doc_id,
                    chunk_index=idx,
                    content=content,
                    char_count=len(content),
                    position=pos,
                    chunk_meta=meta,
                )
            )
        await session.commit()
    return doc_id


async def _seed_screening_record(
    session_factory: async_sessionmaker[AsyncSession],
    user_id: uuid.UUID,
    *,
    title: str = "Abstract Only Paper",
    abstract: str = "We evaluated 120 patients in a randomized controlled trial.",
    doi: str | None = "10.1000/test.2026",
) -> tuple[uuid.UUID, uuid.UUID]:
    task_id = uuid.uuid4()
    log_id = uuid.uuid4()
    rec_id = uuid.uuid4()
    async with session_factory() as session:
        session.add(
            Task(
                id=task_id,
                user_id=user_id,
                title="PRISMA 任务",
                description="筛选任务",
                status="completed",
            )
        )
        session.add(
            SearchLog(
                id=log_id,
                task_id=task_id,
                query="rag",
                sources=["arxiv"],
                hits_total=1,
                hits_by_source={"arxiv": 1},
                hits_deduped=1,
                executed_at=datetime.now(UTC),
            )
        )
        session.add(
            ScreeningRecord(
                id=rec_id,
                search_log_id=log_id,
                task_id=task_id,
                source="arxiv",
                source_id=f"arx-{rec_id.hex[:8]}",
                title=title,
                authors=["Alice"],
                abstract=abstract,
                doi=doi,
                current_stage=DecisionStage.INCLUDED.value,
            )
        )
        await session.commit()
    return task_id, rec_id


class TestTableExtractorUnit:
    """TableExtractor 核心逻辑：子串校验、无摘要跳过 LLM、定位继承与并发隔离。"""

    @pytest.mark.asyncio
    async def test_hallucinated_quote_cleared_and_valid_quote_inherits_locator(
        self, tmp_path
    ) -> None:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'unit.db'}", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            user_id = uuid.uuid4()
            async with session_factory() as session:
                session.add(
                    User(
                        id=user_id,
                        username="u_unit",
                        email="u_unit@example.com",
                        password_hash="hash",
                    )
                )
                await session.commit()

            doc_id = await _seed_knowledge_doc(
                session_factory,
                user_id,
                chunks=[
                    (
                        "1 引言：背景综述段落。",
                        {"start_offset": 0, "end_offset": 12, "page_start": 1, "page_end": 1},
                        {"section_title": "1 引言"},
                    ),
                    (
                        "3.2 实验设置\nTable 2 报告总样本量 N=320，准确率达到 94.2%。",
                        {"start_offset": 14, "end_offset": 56, "page_start": 5, "page_end": 5},
                        {"section_title": "3.2 实验设置", "table_ref": "Table 2"},
                    ),
                ],
            )
            _, empty_abs_rec_id = await _seed_screening_record(
                session_factory, user_id, title="No Abstract Paper", abstract="   "
            )

            table_id = uuid.uuid4()
            async with session_factory() as session:
                session.add(
                    ExtractionTable(
                        id=table_id,
                        user_id=user_id,
                        title="单元测试抽取表",
                        columns=[
                            {"key": "sample_size", "label": "样本量", "prompt": "提取总样本量 N"},
                            {"key": "method", "label": "核心方法", "prompt": "提取核心算法"},
                        ],
                        sources=[
                            {
                                "source_type": "knowledge_doc",
                                "source_id": str(doc_id),
                                "title": "多智能体 RAG 评测研究",
                                "year": 2026,
                                "source_url": None,
                            },
                            {
                                "source_type": "screening_record",
                                "source_id": str(empty_abs_rec_id),
                                "title": "No Abstract Paper",
                                "year": None,
                                "source_url": None,
                            },
                        ],
                        status="draft",
                    )
                )
                await session.commit()

            def _reply(payload: dict[str, Any]) -> list[dict[str, Any]]:
                # 找到包含 N=320 的 passage_id
                target_pid = "P1"
                for p in payload["passages"]:
                    if "N=320" in p["text"]:
                        target_pid = p["passage_id"]
                return [
                    {
                        "column_key": "sample_size",
                        "status": "extracted",
                        "value": "N=320",
                        "passage_id": target_pid,
                        # 刻意加入额外空白验证归一化匹配
                        "quote": "Table 2   报告总样本量 N=320",
                        "confidence": 0.95,
                    },
                    {
                        "column_key": "method",
                        "status": "extracted",
                        "value": "虚构的注意力机制",
                        "passage_id": target_pid,
                        # 幻觉引文：不存在于任何片段中，应被清空为 None
                        "quote": "这是一段模型凭空捏造的原文句子",
                        "confidence": 0.6,
                    },
                ]

            llm = _ScriptedExtractionLLM(_reply)
            extractor = TableExtractor(llm)

            async with session_factory() as session:
                finished = await extractor.run(session, table_id, user_id)
                assert finished.status == "completed"
                cells = (
                    (
                        await session.execute(
                            select(ExtractionCell).where(ExtractionCell.table_id == table_id)
                        )
                    )
                    .scalars()
                    .all()
                )

            # 无摘要的 ScreeningRecord 绝不触发 LLM：总共只调用了 1 次（仅 knowledge_doc）
            assert len(llm.calls) == 1

            by_key = {(c.source_id, c.column_key): c for c in cells}
            # 1) 真实子串通过校验，且继承 DocChunk.position / chunk_meta 的 locator
            c_size = by_key[(str(doc_id), "sample_size")]
            assert c_size.status == "extracted"
            assert c_size.value == "N=320"
            assert c_size.quote == "Table 2   报告总样本量 N=320"
            assert c_size.evidence_scope == "full_text"
            assert c_size.locator == {
                "page_start": 5,
                "page_end": 5,
                "section_title": "3.2 实验设置",
                "table_ref": "Table 2",
            }

            # 2) 幻觉引句被清空为 None
            c_method = by_key[(str(doc_id), "method")]
            assert c_method.status == "extracted"
            assert c_method.quote is None

            # 3) 无摘要 ScreeningRecord 直接落库 not_reported，locator=None
            c_empty_1 = by_key[(str(empty_abs_rec_id), "sample_size")]
            c_empty_2 = by_key[(str(empty_abs_rec_id), "method")]
            assert c_empty_1.status == "not_reported"
            assert c_empty_1.value is None
            assert c_empty_1.evidence_scope == "abstract_only"
            assert c_empty_1.locator is None
            assert c_empty_2.status == "not_reported"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_single_source_llm_error_does_not_fail_other_rows(self, tmp_path) -> None:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'err.db'}", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            user_id = uuid.uuid4()
            async with session_factory() as session:
                session.add(
                    User(
                        id=user_id,
                        username="u_err",
                        email="u_err@example.com",
                        password_hash="hash",
                    )
                )
                await session.commit()

            doc_ok = await _seed_knowledge_doc(session_factory, user_id, title="OK Paper")
            doc_bad = await _seed_knowledge_doc(session_factory, user_id, title="FAIL Paper")

            table_id = uuid.uuid4()
            async with session_factory() as session:
                session.add(
                    ExtractionTable(
                        id=table_id,
                        user_id=user_id,
                        title="容错测试",
                        columns=[{"key": "sample_size", "label": "样本量", "prompt": "提取样本量"}],
                        sources=[
                            {
                                "source_type": "knowledge_doc",
                                "source_id": str(doc_ok),
                                "title": "OK Paper",
                            },
                            {
                                "source_type": "knowledge_doc",
                                "source_id": str(doc_bad),
                                "title": "FAIL Paper",
                            },
                        ],
                        status="draft",
                    )
                )
                await session.commit()

            def _reply(payload: dict[str, Any]) -> Any:
                if payload["paper_title"] == "FAIL Paper":
                    return RuntimeError("上游限流")
                return [
                    {
                        "column_key": "sample_size",
                        "status": "extracted",
                        "value": "320",
                        "passage_id": "P1",
                        "quote": "N=320",
                        "confidence": 0.9,
                    }
                ]

            extractor = TableExtractor(_ScriptedExtractionLLM(_reply))
            async with session_factory() as session:
                finished = await extractor.run(session, table_id, user_id)
                assert finished.status == "completed"
                cells = (
                    (
                        await session.execute(
                            select(ExtractionCell).where(ExtractionCell.table_id == table_id)
                        )
                    )
                    .scalars()
                    .all()
                )
            by_src = {c.source_id: c for c in cells}
            assert by_src[str(doc_ok)].status == "extracted"
            assert by_src[str(doc_bad)].status == "error"
        finally:
            await engine.dispose()


class TestCsvSafeExport:
    """CSV 导出安全与格式测试：BOM、公式注入防御、RFC 4180 转义。"""

    def test_utf8_bom_formula_escape_and_rfc4180(self) -> None:
        assert _safe_csv_cell("=CMD|' /C calc'!A0") == "'=CMD|' /C calc'!A0"
        assert _safe_csv_cell("+SUM(A1)") == "'+SUM(A1)"
        assert _safe_csv_cell("-2+3") == "'-2+3"
        assert _safe_csv_cell("@HYPERLINK()") == "'@HYPERLINK()"
        assert _safe_csv_cell("\tbad") == "'\tbad"
        assert (
            format_locator_text(
                {
                    "page_start": 5,
                    "page_end": 6,
                    "section_title": "3.2 实验设置",
                    "table_ref": "Table 2",
                }
            )
            == "PDF p.5–6 | 3.2 实验设置 | Table 2"
        )

        fake_table = SimpleNamespace(
            columns=[
                {"key": "formula_col", "label": "指标值"},
                {"key": "missing_col", "label": "局限性"},
            ],
            sources=[
                {
                    "source_type": "knowledge_doc",
                    "source_id": "s1",
                    "title": 'Paper "Alpha", Part 1',
                    "year": 2026,
                }
            ],
            cells=[
                {
                    "source_type": "knowledge_doc",
                    "source_id": "s1",
                    "column_key": "formula_col",
                    "status": "extracted",
                    "value": "=CMD()",
                    "quote": 'He said, "accuracy is 94.2%", in Table 2.',
                    "locator": {
                        "page_start": 5,
                        "page_end": 5,
                        "section_title": "3.2 实验设置",
                        "table_ref": "Table 2",
                    },
                    "evidence_scope": "full_text",
                },
                {
                    "source_type": "knowledge_doc",
                    "source_id": "s1",
                    "column_key": "missing_col",
                    "status": "not_reported",
                    "value": None,
                    "quote": None,
                    "locator": None,
                    "evidence_scope": "full_text",
                },
            ],
        )

        raw_bytes = export_table_csv(fake_table, include_quotes=True)
        # 必须以 UTF-8 BOM 开头
        assert raw_bytes.startswith(b"\xef\xbb\xbf")
        decoded = raw_bytes.decode("utf-8")
        assert decoded.startswith("\ufeff")

        rows = list(csv.reader(io.StringIO(raw_bytes.decode("utf-8-sig"))))
        assert rows[0] == [
            "论文标题",
            "年份",
            "来源类型",
            "证据范围",
            "指标值",
            "指标值_原文引用",
            "指标值_定位",
            "局限性",
            "局限性_原文引用",
            "局限性_定位",
        ]
        assert rows[1][0] == 'Paper "Alpha", Part 1'
        assert rows[1][1] == "2026"
        assert rows[1][3] == "全文"
        assert rows[1][4] == "'=CMD()"
        assert rows[1][5] == 'He said, "accuracy is 94.2%", in Table 2.'
        assert rows[1][6] == "PDF p.5 | 3.2 实验设置 | Table 2"
        assert rows[1][7] == "未报告"

        # include_quotes=False 不输出引用与定位列
        compact = list(
            csv.reader(
                io.StringIO(export_table_csv(fake_table, include_quotes=False).decode("utf-8-sig"))
            )
        )
        assert compact[0] == ["论文标题", "年份", "来源类型", "证据范围", "指标值", "局限性"]
        assert compact[1][4] == "'=CMD()"
        assert compact[1][5] == "未报告"


class TestExtractionApiEndpoints:
    """API 端到端测试：建表、422 校验、跨用户 404 隔离、仅补抽单列不覆盖旧列、CSV 下载。"""

    def test_crud_isolation_validation_partial_rerun_and_export(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        token_a, user_a = _register_and_login(client, "extract_owner_a")
        token_b, _user_b = _register_and_login(client, "extract_other_b")

        session_factory = client.session_factory  # type: ignore[attr-defined]
        doc_a = asyncio.run(_seed_knowledge_doc(session_factory, user_a))
        task_a, rec_a = asyncio.run(_seed_screening_record(session_factory, user_a))

        run_log: list[tuple[str, list[str]]] = []

        async def _fake_background_job(
            table_id: uuid.UUID,
            user_id: uuid.UUID,
            *,
            column_keys: Any = (),
            source_ids: Any = (),
            llm_credential_id: uuid.UUID | None = None,
        ) -> None:
            run_log.append((str(table_id), list(column_keys)))

            def _reply(payload: dict[str, Any]) -> list[dict[str, Any]]:
                out = []
                for col in payload["columns"]:
                    if col["column_key"] == "sample_size":
                        out.append(
                            {
                                "column_key": "sample_size",
                                "status": "extracted",
                                "value": "N=320",
                                "passage_id": "P1",
                                "quote": "N=320",
                                "confidence": 0.9,
                            }
                        )
                    elif col["column_key"] == "code_url":
                        out.append(
                            {
                                "column_key": "code_url",
                                "status": "not_reported",
                                "value": None,
                                "passage_id": None,
                                "quote": None,
                                "confidence": 0.8,
                            }
                        )
                    else:
                        out.append(
                            {
                                "column_key": col["column_key"],
                                "status": "extracted",
                                "value": "94.2%",
                                "passage_id": "P1",
                                "quote": "94.2%",
                                "confidence": 0.88,
                            }
                        )
                return out

            async with session_factory() as session:
                await TableExtractor(_ScriptedExtractionLLM(_reply)).run(
                    session,
                    table_id,
                    user_id,
                    column_keys=list(column_keys),
                    source_ids=list(source_ids),
                )

        monkeypatch.setattr(extraction_endpoints, "run_extraction_job", _fake_background_job)

        # 1) 契约校验：空列、超限 13 列、reference_library、extra 字段均 422
        assert (
            client.post(
                "/api/v1/extractions",
                headers=_auth(token_a),
                json={
                    "title": "空列",
                    "columns": [],
                    "sources": [{"source_type": "knowledge_doc", "source_id": str(doc_a)}],
                },
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/v1/extractions",
                headers=_auth(token_a),
                json={
                    "title": "超限列",
                    "columns": [
                        {"key": f"col_{i}", "label": f"列{i}", "prompt": "提取信息"}
                        for i in range(13)
                    ],
                    "sources": [{"source_type": "knowledge_doc", "source_id": str(doc_a)}],
                },
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/v1/extractions",
                headers=_auth(token_a),
                json={
                    "title": "禁止引用库",
                    "columns": [{"key": "sample_size", "label": "样本量", "prompt": "提取样本量"}],
                    "sources": [{"source_type": "reference_library", "source_id": str(doc_a)}],
                },
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/v1/extractions",
                headers=_auth(token_a),
                json={
                    "title": "未知字段",
                    "columns": [{"key": "sample_size", "label": "样本量", "prompt": "提取样本量"}],
                    "sources": [{"source_type": "knowledge_doc", "source_id": str(doc_a)}],
                    "unexpected_field": 123,
                },
            ).status_code
            == 422
        )

        # 2) 跨用户用 A 的文档建表 → 404
        assert (
            client.post(
                "/api/v1/extractions",
                headers=_auth(token_b),
                json={
                    "title": "越权表",
                    "columns": [{"key": "sample_size", "label": "样本量", "prompt": "提取样本量"}],
                    "sources": [{"source_type": "knowledge_doc", "source_id": str(doc_a)}],
                },
            ).status_code
            == 404
        )

        # 3) 用户 A 正常创建并自动执行抽取（含 knowledge_doc + screening_record）
        created = client.post(
            "/api/v1/extractions",
            headers=_auth(token_a),
            json={
                "title": "对比实验表",
                "task_id": str(task_a),
                "columns": [
                    {"key": "sample_size", "label": "样本量", "prompt": "提取样本量"},
                    {"key": "metrics", "label": "指标", "prompt": "提取主指标"},
                ],
                "sources": [
                    {"source_type": "knowledge_doc", "source_id": str(doc_a)},
                    {"source_type": "screening_record", "source_id": str(rec_a)},
                ],
                "auto_run": True,
            },
        )
        assert created.status_code == 201, created.text
        table_id = created.json()["id"]
        assert created.json()["status"] == "running"

        # 后台任务在 TestClient 返回后已完成，查详情验证结果
        detail = client.get(f"/api/v1/extractions/{table_id}", headers=_auth(token_a))
        assert detail.status_code == 200
        body = detail.json()
        assert body["status"] == "completed"
        cells_before = {(c["source_id"], c["column_key"]): c for c in body["cells"]}
        assert cells_before[(str(doc_a), "sample_size")]["value"] == "N=320"
        assert cells_before[(str(doc_a), "sample_size")]["locator"]["page_start"] == 5
        assert cells_before[(str(rec_a), "sample_size")]["evidence_scope"] == "abstract_only"
        assert cells_before[(str(rec_a), "sample_size")]["locator"] is None

        # 4) 跨用户隔离：用户 B 访问全部端点统一 404
        assert (
            client.get(f"/api/v1/extractions/{table_id}", headers=_auth(token_b)).status_code == 404
        )
        assert (
            client.post(
                f"/api/v1/extractions/{table_id}/run",
                headers=_auth(token_b),
                json={},
            ).status_code
            == 404
        )
        assert (
            client.get(
                f"/api/v1/extractions/{table_id}/export?format=csv",
                headers=_auth(token_b),
            ).status_code
            == 404
        )
        assert (
            client.delete(f"/api/v1/extractions/{table_id}", headers=_auth(token_b)).status_code
            == 404
        )

        # 5) 追加第 3 列，仅对第 3 列触发 /run —— 不得覆盖原有 sample_size / metrics 列
        add_res = client.post(
            f"/api/v1/extractions/{table_id}/columns",
            headers=_auth(token_a),
            json={
                "column": {"key": "code_url", "label": "开源代码", "prompt": "提取代码仓库地址"},
                "auto_run": False,
            },
        )
        assert add_res.status_code == 200, add_res.text

        rerun = client.post(
            f"/api/v1/extractions/{table_id}/run",
            headers=_auth(token_a),
            json={"column_keys": ["code_url"]},
        )
        assert rerun.status_code == 202, rerun.text
        assert run_log[-1] == (table_id, ["code_url"])

        after_detail = client.get(f"/api/v1/extractions/{table_id}", headers=_auth(token_a)).json()
        cells_after = {(c["source_id"], c["column_key"]): c for c in after_detail["cells"]}
        assert cells_after[(str(doc_a), "code_url")]["status"] == "not_reported"
        # 原有两列值、引文与定位完整保留未被覆盖
        assert cells_after[(str(doc_a), "sample_size")] == cells_before[(str(doc_a), "sample_size")]
        assert cells_after[(str(doc_a), "metrics")] == cells_before[(str(doc_a), "metrics")]

        # 6) 列表过滤与 CSV 导出接口
        listed = client.get(f"/api/v1/extractions?task_id={task_a}", headers=_auth(token_a))
        assert listed.status_code == 200
        assert len(listed.json()["items"]) == 1
        assert listed.json()["items"][0]["column_count"] == 3

        exported = client.get(
            f"/api/v1/extractions/{table_id}/export?format=csv&include_quotes=true",
            headers=_auth(token_a),
        )
        assert exported.status_code == 200
        assert exported.content.startswith(b"\xef\xbb\xbf")
        assert "attachment; filename*=UTF-8''" in exported.headers["content-disposition"]
        assert "N=320" in exported.content.decode("utf-8-sig")

        # 7) 删除后 404
        assert (
            client.delete(f"/api/v1/extractions/{table_id}", headers=_auth(token_a)).status_code
            == 204
        )
        assert (
            client.get(f"/api/v1/extractions/{table_id}", headers=_auth(token_a)).status_code == 404
        )
