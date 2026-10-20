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
        assert len(items) == 1
        assert items[0]["name"] == "模板A"

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
