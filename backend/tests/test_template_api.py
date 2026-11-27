"""Word 模板 HTTP 端点测试（Phase 1 P1-A 接线）。

对应计划 `2026-10-20-00-phase1-foundation-plan.md` 的「遗留：HTTP 端点未接线」。

端点：
    POST   /api/v1/templates            上传 .docx 模板（multipart `file`）
    GET    /api/v1/templates            列出当前用户模板（含占位符与 style_map）
    GET    /api/v1/templates/{id}       模板详情
    DELETE /api/v1/templates/{id}       删除（owner-scoped）
    POST   /api/v1/templates/{id}/render  用模板渲染成稿（返回 .docx 下载）

验收要点：
- 只接受 `.docx`；`.docm`/含宏/zip 炸弹 → 400（复用 inspector 的安全校验）；
- 跨用户访问统一 404（不泄漏存在性）；
- 渲染的参考文献由调用方传入**已 CSL 渲染的行**（本端点不碰引用引擎）；
- 上传后能拿回占位符清单与 style_map（供前端做映射）。
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.v1.endpoints import templates as templates_endpoint
from app.db.base import Base
from app.db.session import get_db


def _minimal_docx(*, placeholders: list[str] | None = None, with_macro: bool = False) -> bytes:
    """构造最小合法 .docx（可选占位符与宏部件）。

    注意：inspector 要求同时存在 `word/document.xml` **与** `word/styles.xml`，
    因此 fixture 必须带一个最小 styles.xml（含标题/正文样式，供 style_map 推断）。
    """
    body_paragraphs = "".join(
        f"<w:p><w:r><w:t>{{{{ {key} }}}}</w:t></w:r></w:p>" for key in (placeholders or [])
    )
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body_paragraphs}<w:p><w:r><w:t>固定内容</w:t></w:r></w:p></w:body>"
        "</w:document>"
    )
    styles_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/></w:style>'
        '<w:style w:type="paragraph" w:styleId="Normal"><w:name w:val="Normal"/></w:style>'
        "</w:styles>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
        'officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-'
        'officedocument.wordprocessingml.styles+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/officeDocument" Target="word/document.xml"/>'
        "</Relationships>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("word/document.xml", document_xml)
        archive.writestr("word/styles.xml", styles_xml)
        if with_macro:
            archive.writestr("word/vbaProject.bin", b"\x00" * 16)
    return buffer.getvalue()


@pytest.fixture
def app(tmp_path: Path) -> FastAPI:
    """独立应用：挂模板路由 + 独立 SQLite + 独立存储根。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _get_db() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    application = FastAPI()
    application.include_router(templates_endpoint.router, prefix="/api/v1")

    # 与生产一致的异常映射（生产在 app/main.py 注册全局 handler；独立测试 app 需自备）
    from app.core.exceptions import AppException, NotFoundException

    @application.exception_handler(NotFoundException)
    async def _handle_not_found(_: Any, exc: NotFoundException) -> Any:
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=404, content={"detail": exc.message})

    @application.exception_handler(AppException)
    async def _handle_app(_: Any, exc: AppException) -> Any:
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=exc.status_code,
            content={"error_code": exc.error_code, "message": exc.message},
        )

    application.dependency_overrides[get_db] = _get_db
    application.dependency_overrides[templates_endpoint.get_template_storage_root] = (
        lambda: tmp_path / "templates"
    )
    # 测试绕过真实鉴权：直接注入固定用户
    application.dependency_overrides[templates_endpoint.get_current_active_user] = _fake_user
    application.state.test_engine = engine  # type: ignore[attr-defined]
    return application


class _FakeUser:
    id = __import__("uuid").uuid4()
    username = "tester"
    email = "tester@example.com"
    is_active = True
    role = "user"


def _fake_user() -> Any:
    return _FakeUser()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    """建表后返回客户端。

    ⚠️ 不要在 fixture 里用 `asyncio.get_event_loop()`：全量运行时时该线程可能没有
    当前事件循环（pytest-asyncio 会接管/关闭），会报
    `RuntimeError: There is no current event loop in thread 'MainThread'`。
    改用 `asyncio.run` 在独立循环里建表。
    """
    import asyncio

    engine = app.state.test_engine  # type: ignore[attr-defined]

    async def _create() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    asyncio.run(_create())
    with TestClient(app) as test_client:
        yield test_client


class TestUploadTemplate:
    def test_upload_returns_placeholders_and_style_map(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/templates",
            files={
                "file": (
                    "school.docx",
                    _minimal_docx(placeholders=["title", "body"]),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
            data={"name": "学院模板"},
        )

        assert response.status_code == 201, response.text
        payload = response.json()
        assert payload["name"] == "学院模板"
        assert {item["key"] for item in payload["placeholders"]} == {"title", "body"}
        assert isinstance(payload["style_map"], dict)

    def test_rejects_macro_template(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/templates",
            files={
                "file": ("evil.docx", _minimal_docx(with_macro=True), "application/octet-stream")
            },
            data={"name": "带宏"},
        )

        assert response.status_code == 400
        assert "宏" in response.json()["detail"] or "vba" in response.json()["detail"].lower()

    def test_rejects_non_docx_extension(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/templates",
            files={"file": ("evil.docm", _minimal_docx(), "application/octet-stream")},
            data={"name": "docm"},
        )

        assert response.status_code == 400

    def test_rejects_empty_file(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/templates",
            files={"file": ("empty.docx", b"", "application/octet-stream")},
            data={"name": "空"},
        )

        assert response.status_code == 400


class TestListAndGet:
    def test_list_returns_uploaded_templates(self, client: TestClient) -> None:
        client.post(
            "/api/v1/templates",
            files={
                "file": (
                    "a.docx",
                    _minimal_docx(placeholders=["title"]),
                    "application/octet-stream",
                )
            },
            data={"name": "模板A"},
        )

        response = client.get("/api/v1/templates")

        assert response.status_code == 200
        items = response.json()["items"]
        # 包含两个内置母版（文献综述 + 开题报告）+ 用户上传的模板A
        assert len(items) == 3
        assert items[0]["template_id"] == "builtin:literature_review_cn_standard"
        assert items[0]["source"] == "builtin"
        assert items[1]["template_id"] == "builtin:thesis_proposal_cn_standard"
        assert items[1]["source"] == "builtin"
        assert items[2]["name"] == "模板A"
        assert items[2]["source"] == "user"

    def test_get_builtin_template_returns_metadata(self, client: TestClient) -> None:
        response = client.get("/api/v1/templates/builtin:literature_review_cn_standard")
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["source"] == "builtin"
        assert {p["key"] for p in data["placeholders"]} >= {"title", "author", "body", "references"}

    def test_delete_builtin_template_returns_400(self, client: TestClient) -> None:
        response = client.delete("/api/v1/templates/builtin:literature_review_cn_standard")
        assert response.status_code == 400
        assert "内置" in response.json()["detail"]

    def test_get_unknown_template_returns_404(self, client: TestClient) -> None:
        response = client.get("/api/v1/templates/not-exists")

        assert response.status_code == 404


class TestRender:
    def test_render_fills_sections_and_bibliography(self, client: TestClient) -> None:
        created = client.post(
            "/api/v1/templates",
            files={
                "file": (
                    "t.docx",
                    _minimal_docx(placeholders=["title", "body", "references"]),
                    "application/octet-stream",
                )
            },
            data={"name": "渲染用"},
        )
        template_id = created.json()["template_id"]

        response = client.post(
            f"/api/v1/templates/{template_id}/render",
            json={
                "sections": {"title": "我的综述", "body": "正文第一段"},
                "bibliography": ["[1] 张三. 论文标题[J]. 期刊, 2024."],
            },
        )

        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith(
            "application/vnd.openxmlformats-officedocument"
        )
        # 产物必须是可打开的 docx，且含填入的文字
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            document = archive.read("word/document.xml").decode("utf-8")
        assert "我的综述" in document
        assert "正文第一段" in document
        assert "张三" in document

    def test_render_unknown_template_returns_404(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/templates/not-exists/render",
            json={"sections": {"title": "x"}, "bibliography": []},
        )

        assert response.status_code == 404

    def test_render_missing_required_section_returns_400(self, client: TestClient) -> None:
        """模板要求 title 但未提供 → 400（严格契约，不静默丢内容）。"""
        created = client.post(
            "/api/v1/templates",
            files={
                "file": (
                    "t2.docx",
                    _minimal_docx(placeholders=["title"]),
                    "application/octet-stream",
                )
            },
            data={"name": "严格"},
        )
        template_id = created.json()["template_id"]

        response = client.post(
            f"/api/v1/templates/{template_id}/render",
            json={"sections": {}, "bibliography": []},
        )

        assert response.status_code == 400

    def test_render_builtin_template_with_bibliography_entries_embeds_chart(
        self, client: TestClient
    ) -> None:
        response = client.post(
            "/api/v1/templates/builtin:literature_review_cn_standard/render",
            json={
                "sections": {
                    "title": "检索增强生成综述",
                    "author": "李四",
                    "advisor": "王五",
                    "major": "软件工程",
                    "college": "软件学院",
                    "body": "# 1 研究现状\n\n相关工作进展迅速。\n\n# 2 总结\n\n展望未来。",
                },
                "bibliography": ["[1] 文献A", "[2] 文献B", "[3] 文献C"],
                "bibliography_entries": [
                    {"year": 2022},
                    {"year": 2023},
                    {"year": 2024},
                ],
                "filename": "thesis_review.docx",
            },
        )
        assert response.status_code == 200, response.text
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            names = archive.namelist()
            document = archive.read("word/document.xml").decode("utf-8")
        assert "检索增强生成综述" in document
        assert "李四" in document
        assert any(name.startswith("word/media/") for name in names)

    def test_render_proposal_template_embeds_roadmap_figure(self, client: TestClient) -> None:
        """开题报告母版：技术路线 4 步 → 自动插图，正文不留 fig:// 死链。"""
        body = (
            "# 1 问题提出的背景\n\n"
            "现有研究在中文语料上覆盖不足。\n\n"
            "# 2 论文的主要内容和技术路线\n\n"
            "## 2.2 技术路线\n\n"
            "1. 需求分析：梳理指标与场景\n"
            "2. 系统设计：确定低功耗架构\n"
            "3. 系统实现：完成算法与联调\n"
            "4. 实验验证：对比基线方案\n\n"
            "## 2.3 可行性分析\n\n技术、经济与进度均可行。\n\n"
            "# 3 研究计划进度安排及预期目标\n\n"
            "| 起止时间 | 研究阶段 | 主要工作内容 | 阶段性成果 |\n"
            "| --- | --- | --- | --- |\n"
            "| 2026.01-2026.03 | 需求分析 | 文献调研 | 开题报告 |\n"
        )
        response = client.post(
            "/api/v1/templates/builtin:thesis_proposal_cn_standard/render",
            json={
                "sections": {
                    "title": "基于 STM32 的低功耗运动手环心率监测系统设计",
                    "author": "张三",
                    "advisor": "李四",
                    "major": "计算机科学与技术",
                    "college": "计算机学院",
                    "body": body,
                },
                "bibliography": ["[1] 文献A", "[2] 文献B"],
                "filename": "proposal.docx",
            },
        )

        assert response.status_code == 200, response.text
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            names = archive.namelist()
            document = archive.read("word/document.xml").decode("utf-8")
        assert any(name.startswith("word/media/") for name in names)
        assert "fig://" not in document
        assert "技术路线" in document

    def test_render_proposal_template_without_roadmap_leaves_no_dead_link(
        self, client: TestClient
    ) -> None:
        body = (
            "# 1 问题提出的背景\n\n现有研究仍有空白。\n\n# 2 论文的主要内容和技术路线\n\n内容待定。"
        )
        response = client.post(
            "/api/v1/templates/builtin:thesis_proposal_cn_standard/render",
            json={
                "sections": {
                    "title": "开题报告主题",
                    "author": "张三",
                    "advisor": "李四",
                    "major": "软件工程",
                    "college": "软件学院",
                    "body": body,
                },
                "bibliography": [],
                "filename": "proposal.docx",
            },
        )

        assert response.status_code == 200, response.text
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            document = archive.read("word/document.xml").decode("utf-8")
        assert "fig://" not in document

    def test_render_with_chinese_filename_sets_rfc5987_content_disposition(
        self, client: TestClient
    ) -> None:
        """含中文文件名不得因 HTTP header latin-1 编码抛 500（线上回归）。"""
        response = client.post(
            "/api/v1/templates/builtin:literature_review_cn_standard/render",
            json={
                "sections": {
                    "title": "基于STM32运动手环综述",
                    "author": "张三",
                    "advisor": "李四 教授",
                    "major": "计算机科学与技术",
                    "college": "计算机学院",
                    "body": "# 研究现状\n\n相关工作进展迅速。",
                },
                "bibliography": ["[1] 文献A"],
                "filename": "基于STM32运动手环设计与实现-高校标准综述.docx",
            },
        )
        assert response.status_code == 200, response.text
        disposition = response.headers.get("content-disposition", "")
        assert disposition.startswith("attachment;")
        assert "filename*=UTF-8''" in disposition
        assert "%E9%AB%98%E6%A0%A1%E6%A0%87%E5%87%86%E7%BB%BC%E8%BF%B0.docx" in disposition
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            assert "word/document.xml" in archive.namelist()


class TestDelete:
    def test_delete_removes_template(self, client: TestClient) -> None:
        created = client.post(
            "/api/v1/templates",
            files={"file": ("d.docx", _minimal_docx(), "application/octet-stream")},
            data={"name": "待删"},
        )
        template_id = created.json()["template_id"]

        assert client.delete(f"/api/v1/templates/{template_id}").status_code == 204
        assert client.get(f"/api/v1/templates/{template_id}").status_code == 404

    def test_delete_unknown_returns_404(self, client: TestClient) -> None:
        assert client.delete("/api/v1/templates/nope").status_code == 404
