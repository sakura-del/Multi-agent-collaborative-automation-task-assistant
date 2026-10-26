"""引用样式 API 契约测试（Issue #4 M2-2 / 计划 §1.2、§1.3）。

覆盖：
- 列表 = 5 个预置样式（`source="preset"`，默认样式在首位）+ 当前用户自定义（`source="custom"`）；
- 上传合法 `.csl` → 201 + 落盘 + 入库；CSL-M / 超大 / 非 UTF-8 / 坏 XML → **400 且 detail 可读**；
- 同内容重复上传 → 幂等（不产生重复行）；同 `<info><id>` 不同内容 → 服务端加 `-2` 后缀；
- 客户端文件名绝不参与落盘路径（防路径穿越）；
- 删除自定义 → 204 + 文件消失；预置不可删（400）；不存在/跨用户 → 404；
- owner 隔离：A 上传的样式 B 看不到、删不掉。

测试不依赖 Lead 的 `router.py`：用 `create_app()` 构造真实应用（复用全局 AppException →
HTTP 映射），只额外 include 本端点；DB 为临时文件型 SQLite（WAL），鉴权用最小依赖覆盖，
因此**不产生任何外部网络/真实 PostgreSQL 依赖**。
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.db.models  # noqa: F401  确保全部 ORM 注册到 Base.metadata
from app.api.deps import get_current_user
from app.api.v1.endpoints.citation_styles import (
    MAX_STYLE_BYTES,
    get_csl_storage_root,
)
from app.api.v1.endpoints.citation_styles import (
    router as citation_styles_router,
)
from app.core.exceptions import UnauthorizedException, ValidationException
from app.db.base import Base
from app.db.models.citation_style import CitationStyle  # noqa: F401  注册 citation_style 表
from app.db.session import get_db
from app.knowledge.services.citation_style_service import CitationStyleService
from app.tools.csl.models import DEFAULT_STYLE_ID

USER_A = UUID("11111111-1111-1111-1111-111111111111")
USER_B = UUID("22222222-2222-2222-2222-222222222222")
TOKEN_A = "token-a"
TOKEN_B = "token-b"

PRESET_IDS = (
    "gbt7714-2025-numeric",
    "gbt7714-2015-numeric",
    "gbt7714-2025-author-date",
    "apa",
    "ieee",
    "nature",
)

API = "/api/v1/citation-styles"


# ===== CSL 测试素材 =====


def make_csl(
    *,
    style_id: str = "my-custom-style",
    title: str = "我的自定义样式",
    default_locale: str = "zh-CN",
    numbering: bool = True,
    extra_info: str = "",
) -> str:
    """最小合法标准 CSL 1.0.2 样式（可选编号制）；`extra_info` 用于注入 CSL-M 构造。"""
    citation = (
        '<citation><layout><text variable="citation-number" prefix="[" suffix="]"/></layout>'
        "</citation>"
        if numbering
        else '<citation><layout><text variable="title"/></layout></citation>'
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<style xmlns="http://purl.org/net/xbiblio/csl" class="in-text" version="1.0"'
        f' default-locale="{default_locale}">\n'
        "  <info>\n"
        f"    <title>{title}</title>\n"
        f"    <id>{style_id}</id>\n"
        f"{extra_info}"
        "  </info>\n"
        f"  {citation}\n"
        '  <bibliography><layout><text variable="title"/></layout></bibliography>\n'
        "</style>\n"
    )


#: CSL-M 私有构造（`<institution>`）：必须被准入校验显式拒绝
CSL_M_STYLE = make_csl(extra_info="    <institution/>\n")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ===== 夹具 =====


class _StubUser:
    """鉴权替身：只提供依赖与 owner 过滤所需的字段。"""

    def __init__(self, user_id: UUID) -> None:
        self.id = user_id
        self.is_active = True


@dataclass
class StyleEnv:
    """测试环境：真实应用 + 临时库 + 存储根。"""

    client: TestClient
    storage_root: Path
    db_path: Path
    app: FastAPI


@pytest.fixture
def style_env(tmp_path: Path) -> Iterator[StyleEnv]:
    """最小应用（create_app + 本端点）：临时 SQLite + 临时样式存储根。"""
    from app.main import create_app

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'styles.db'}",
        connect_args={"timeout": 10},
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    created = {"done": False}

    async def override_get_db() -> Any:
        if not created["done"]:
            async with engine.begin() as connection:
                await connection.exec_driver_sql("PRAGMA journal_mode=WAL")
                await connection.run_sync(Base.metadata.create_all)
            created["done"] = True
        async with session_factory() as session:
            yield session

    async def override_get_current_user(request: Request) -> Any:
        """用不透明 token 映射到固定用户；无 token → 401（保留真实鉴权语义）。"""
        header = request.headers.get("Authorization") or ""
        token = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else ""
        if token == TOKEN_A:
            return _StubUser(USER_A)
        if token == TOKEN_B:
            return _StubUser(USER_B)
        raise UnauthorizedException("缺少有效的鉴权令牌")

    app: FastAPI = create_app()
    app.include_router(citation_styles_router, prefix="/api/v1")
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user

    client = TestClient(app)
    client.__enter__()
    try:
        yield StyleEnv(
            client=client,
            storage_root=tmp_path / "csl_styles",
            db_path=tmp_path / "styles.db",
            app=app,
        )
    finally:
        client.__exit__(None, None, None)
        app.dependency_overrides.clear()
        asyncio.run(engine.dispose())


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _upload(
    env: StyleEnv,
    *,
    token: str = TOKEN_A,
    content: str | bytes = "",
    filename: str = "my-style.csl",
) -> Any:
    payload = content.encode("utf-8") if isinstance(content, str) else content
    return env.client.post(
        API,
        files={"file": (filename, payload, "text/xml")},
        headers=_auth(token),
    )


def _custom_styles(env: StyleEnv, token: str = TOKEN_A) -> list[dict[str, Any]]:
    resp = env.client.get(API, headers=_auth(token))
    assert resp.status_code == 200
    return [item for item in resp.json()["styles"] if item["source"] == "custom"]


def _stored_files(env: StyleEnv) -> list[str]:
    if not env.storage_root.exists():
        return []
    return sorted(
        path.relative_to(env.storage_root).as_posix() for path in env.storage_root.rglob("*.csl")
    )


# ===== 列表 =====


class TestListStyles:
    def test_lists_all_presets_with_default_first(self, style_env: StyleEnv) -> None:
        resp = style_env.client.get(API, headers=_auth(TOKEN_A))

        assert resp.status_code == 200
        body = resp.json()
        assert body["default_style_id"] == DEFAULT_STYLE_ID == "gbt7714-2025-numeric"
        assert [item["style_id"] for item in body["styles"]] == list(PRESET_IDS)
        assert all(item["source"] == "preset" for item in body["styles"])
        assert body["styles"][0]["is_default"] is True
        assert all(item["is_default"] is False for item in body["styles"][1:])
        # 元数据齐备（前端下拉直接消费）
        first = body["styles"][0]
        assert first["title"]
        assert first["numbering"] == "citation-number"
        assert first["ordering"] == "citation-order"
        assert first["default_locale"] == "zh-CN"

    def test_list_requires_authentication(self, style_env: StyleEnv) -> None:
        assert style_env.client.get(API).status_code == 401


# ===== 上传：成功路径 =====


class TestUploadStyle:
    def test_upload_valid_style_returns_201_and_writes_file(self, style_env: StyleEnv) -> None:
        content = make_csl()
        resp = _upload(style_env, content=content)

        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["style_id"] == "my-custom-style"
        assert body["title"] == "我的自定义样式"
        assert body["numbering"] == "citation-number"
        assert body["ordering"] == "citation-order"
        assert body["default_locale"] == "zh-CN"
        assert body["source"] == "custom"
        assert body["is_default"] is False

        target = style_env.storage_root / str(USER_A) / "my-custom-style.csl"
        assert target.is_file()
        assert target.read_text(encoding="utf-8") == content

        styles = _custom_styles(style_env)
        assert [item["style_id"] for item in styles] == ["my-custom-style"]

    def test_presets_stay_first_after_upload(self, style_env: StyleEnv) -> None:
        _upload(style_env, content=make_csl())

        body = style_env.client.get(API, headers=_auth(TOKEN_A)).json()
        assert [item["style_id"] for item in body["styles"]] == [
            *PRESET_IDS,
            "my-custom-style",
        ]
        assert body["styles"][-1]["source"] == "custom"

    def test_upload_falls_back_to_custom_style_id(self, style_env: StyleEnv) -> None:
        """CSL 无 `<info><id>` 时回退 `custom-style`。"""
        content = make_csl().replace("    <id>my-custom-style</id>\n", "")

        resp = _upload(style_env, content=content)

        assert resp.status_code == 201, resp.text
        assert resp.json()["style_id"] == "custom-style"
        assert (style_env.storage_root / str(USER_A) / "custom-style.csl").is_file()

    def test_upload_normalizes_style_id(self, style_env: StyleEnv) -> None:
        """`<info><id>` 归一：小写、非法字符折叠为 `-`、去首尾 `-`。"""
        content = make_csl(style_id="My Style__V2!!", title="归一测试")

        resp = _upload(style_env, content=content)

        assert resp.status_code == 201, resp.text
        assert resp.json()["style_id"] == "my-style-v2"

    def test_client_filename_never_used_for_storage(self, style_env: StyleEnv) -> None:
        """客户端文件名（含穿越片段）绝不参与落盘路径。"""
        resp = _upload(style_env, content=make_csl(), filename="../../../evil.csl")

        assert resp.status_code == 201, resp.text
        assert resp.json()["style_id"] == "my-custom-style"
        assert _stored_files(style_env) == [f"{USER_A}/my-custom-style.csl"]
        assert list(style_env.storage_root.parent.rglob("evil.csl")) == []

    def test_upload_requires_authentication(self, style_env: StyleEnv) -> None:
        resp = style_env.client.post(
            API,
            files={"file": ("a.csl", make_csl().encode("utf-8"), "text/xml")},
        )
        assert resp.status_code == 401


# ===== 上传：准入与格式拒绝（一律 400，绝不静默接受） =====


class TestUploadRejections:
    def test_csl_m_style_rejected_with_reasons(self, style_env: StyleEnv) -> None:
        resp = _upload(style_env, content=CSL_M_STYLE)

        assert resp.status_code == 400, resp.text
        detail = resp.json()["detail"]
        assert isinstance(detail, str)
        assert "institution" in detail
        assert "CSL-M" in detail
        # 行号必须指向原文中的 <institution/>
        assert ":6:" in detail or ":7:" in detail
        # 未静默接受：不落盘、不入库
        assert _stored_files(style_env) == []
        assert _custom_styles(style_env) == []

    def test_oversized_style_rejected(self, style_env: StyleEnv) -> None:
        huge = make_csl() + ("<!-- " + "x" * (MAX_STYLE_BYTES + 1024) + " -->\n")

        resp = _upload(style_env, content=huge)

        assert resp.status_code == 400, resp.text
        assert "上限" in resp.json()["detail"] or "超过" in resp.json()["detail"]
        assert _stored_files(style_env) == []

    def test_non_utf8_style_rejected(self, style_env: StyleEnv) -> None:
        resp = _upload(style_env, content=b"\xff\xfe\x00\x01not-utf8")

        assert resp.status_code == 400, resp.text
        assert "UTF-8" in resp.json()["detail"].upper()
        assert _stored_files(style_env) == []

    def test_broken_xml_rejected(self, style_env: StyleEnv) -> None:
        resp = _upload(style_env, content='<style version="1.0"><info>')

        assert resp.status_code == 400, resp.text
        assert "XML" in resp.json()["detail"]
        assert _stored_files(style_env) == []

    def test_missing_required_structure_rejected(self, style_env: StyleEnv) -> None:
        no_title = make_csl().replace("<title>我的自定义样式</title>", "")

        resp = _upload(style_env, content=no_title)

        assert resp.status_code == 400, resp.text
        assert "title" in resp.json()["detail"]
        assert _stored_files(style_env) == []


# ===== 幂等与命名冲突 =====


class TestUploadIdempotency:
    def test_duplicate_content_is_idempotent(self, style_env: StyleEnv) -> None:
        content = make_csl()

        first = _upload(style_env, content=content)
        second = _upload(style_env, content=content, filename="another-name.csl")

        assert first.status_code == 201, first.text
        assert second.status_code == 201, second.text
        assert second.json()["style_id"] == first.json()["style_id"]
        assert _custom_styles(style_env) == [second.json()]
        assert _stored_files(style_env) == [f"{USER_A}/my-custom-style.csl"]

    def test_same_style_id_different_content_gets_suffix(self, style_env: StyleEnv) -> None:
        first = _upload(style_env, content=make_csl(title="第一版"))
        second = _upload(style_env, content=make_csl(title="第二版"))

        assert first.status_code == 201, first.text
        assert second.status_code == 201, second.text
        assert first.json()["style_id"] == "my-custom-style"
        assert second.json()["style_id"] == "my-custom-style-2"
        # 列表顺序按 created_at DESC（同秒内时间戳相同 → 不断言相对顺序），两条都必须存在
        assert sorted(item["style_id"] for item in _custom_styles(style_env)) == [
            "my-custom-style",
            "my-custom-style-2",
        ]
        assert _stored_files(style_env) == [
            f"{USER_A}/my-custom-style-2.csl",
            f"{USER_A}/my-custom-style.csl",
        ]

    def test_preset_style_id_is_not_shadowed(self, style_env: StyleEnv) -> None:
        resp = _upload(style_env, content=make_csl(style_id="apa", title="我的 APA 变体"))

        assert resp.status_code == 201, resp.text
        assert resp.json()["style_id"] == "apa-2"
        # 预置 apa 仍在列表中且仍是预置来源
        styles = style_env.client.get(API, headers=_auth(TOKEN_A)).json()["styles"]
        apa = next(item for item in styles if item["style_id"] == "apa")
        assert apa["source"] == "preset"
        assert apa["title"].startswith("APA")


# ===== 删除 =====


class TestSharedRegistryWiring:
    """上传/删除与共享注册表的接线（计划 §1.4）：注册后渲染期可解析，删除后注销。"""

    def test_uploaded_style_is_resolvable_and_delete_unregisters(self, style_env: StyleEnv) -> None:
        from app.tools.csl.styles import StyleRegistry

        registry = StyleRegistry()
        style_env.app.state.style_registry = registry

        resp = _upload(style_env, content=make_csl())

        assert resp.status_code == 201, resp.text
        assert registry.resolve("my-custom-style") == "my-custom-style"
        assert registry.meta("my-custom-style").title == "我的自定义样式"
        assert registry.get("my-custom-style").is_file()

        deleted = style_env.client.delete(f"{API}/my-custom-style", headers=_auth(TOKEN_A))

        assert deleted.status_code == 204, deleted.text
        assert registry.custom_style_ids() == []


class TestDeleteStyle:
    def test_delete_custom_removes_row_and_file(self, style_env: StyleEnv) -> None:
        _upload(style_env, content=make_csl())

        resp = style_env.client.delete(f"{API}/my-custom-style", headers=_auth(TOKEN_A))

        assert resp.status_code == 204, resp.text
        assert _custom_styles(style_env) == []
        assert not (style_env.storage_root / str(USER_A) / "my-custom-style.csl").exists()

    def test_delete_preset_rejected(self, style_env: StyleEnv) -> None:
        resp = style_env.client.delete(f"{API}/apa", headers=_auth(TOKEN_A))

        assert resp.status_code == 400, resp.text
        assert "预置" in resp.json()["detail"]
        # 预置样式仍然在列表里
        styles = style_env.client.get(API, headers=_auth(TOKEN_A)).json()["styles"]
        assert "apa" in [item["style_id"] for item in styles]

    def test_delete_unknown_returns_404(self, style_env: StyleEnv) -> None:
        resp = style_env.client.delete(f"{API}/no-such-style", headers=_auth(TOKEN_A))
        assert resp.status_code == 404

    def test_delete_requires_authentication(self, style_env: StyleEnv) -> None:
        assert style_env.client.delete(f"{API}/apa").status_code == 401


# ===== owner 隔离 =====


class TestOwnerIsolation:
    def test_other_user_cannot_see_or_delete(self, style_env: StyleEnv) -> None:
        _upload(style_env, token=TOKEN_A, content=make_csl())

        # A 能看到自己的自定义样式；B 看不到
        assert [item["style_id"] for item in _custom_styles(style_env, TOKEN_A)] == [
            "my-custom-style"
        ]
        assert _custom_styles(style_env, TOKEN_B) == []

        # B 删除 A 的样式 → 404（与不存在同语义，不泄漏存在性）
        resp = style_env.client.delete(f"{API}/my-custom-style", headers=_auth(TOKEN_B))
        assert resp.status_code == 404

        # A 的样式完好无损
        assert [item["style_id"] for item in _custom_styles(style_env, TOKEN_A)] == [
            "my-custom-style"
        ]
        assert (style_env.storage_root / str(USER_A) / "my-custom-style.csl").is_file()

    def test_two_users_same_content_are_isolated(self, style_env: StyleEnv) -> None:
        """同内容不同用户各自持有：幂等只在 owner 内生效。"""
        content = make_csl()

        first = _upload(style_env, token=TOKEN_A, content=content)
        second = _upload(style_env, token=TOKEN_B, content=content)

        assert first.status_code == 201, first.text
        assert second.status_code == 201, second.text
        assert first.json()["style_id"] == second.json()["style_id"] == "my-custom-style"
        assert _stored_files(style_env) == [
            f"{USER_A}/my-custom-style.csl",
            f"{USER_B}/my-custom-style.csl",
        ]
        assert _custom_styles(style_env, TOKEN_A) == [first.json()]
        assert _custom_styles(style_env, TOKEN_B) == [second.json()]


# ===== 服务层（不经 HTTP） =====


class TestServiceGuards:
    def test_max_style_bytes_constant(self) -> None:
        assert MAX_STYLE_BYTES == 256 * 1024

    async def test_path_for_rejects_traversal(self, db_session: Any, tmp_path: Path) -> None:
        service = CitationStyleService(db=db_session, storage_root=tmp_path / "csl_styles")

        for bad in ("../evil", "../../etc/passwd", "a/b", ".."):
            with pytest.raises(ValidationException):
                service.path_for(USER_A, bad)

    async def test_path_for_accepts_normal_id(self, db_session: Any, tmp_path: Path) -> None:
        storage_root = tmp_path / "csl_styles"
        service = CitationStyleService(db=db_session, storage_root=storage_root)

        path = service.path_for(USER_A, "my-style")

        assert path == (storage_root / str(USER_A) / "my-style.csl").resolve()
        assert path.is_relative_to(storage_root.resolve())

    async def test_upload_empty_content_rejected(self, db_session: Any, tmp_path: Path) -> None:
        service = CitationStyleService(db=db_session, storage_root=tmp_path / "csl_styles")

        with pytest.raises(ValidationException):
            await service.upload(USER_A, filename="empty.csl", content=b"")

    async def test_upload_is_idempotent_at_db_level(self, db_session: Any, tmp_path: Path) -> None:
        service = CitationStyleService(db=db_session, storage_root=tmp_path / "csl_styles")
        content = make_csl().encode("utf-8")

        first = await service.upload(USER_A, filename="a.csl", content=content)
        second = await service.upload(USER_A, filename="b.csl", content=content)

        assert first.style_id == second.style_id == "my-custom-style"
        rows = await service.list_all(USER_A)
        assert [item.style_id for item in rows.styles if item.source == "custom"] == [
            "my-custom-style"
        ]
        assert rows.default_style_id == DEFAULT_STYLE_ID

    async def test_upload_stores_content_hash_and_size(
        self, db_session: Any, tmp_path: Path
    ) -> None:
        service = CitationStyleService(db=db_session, storage_root=tmp_path / "csl_styles")
        content = make_csl().encode("utf-8")

        await service.upload(USER_A, filename="a.csl", content=content)

        from app.db.repositories.citation_style import CitationStyleRepository

        row = await CitationStyleRepository(db_session).get_for_user(USER_A, "my-custom-style")
        assert row is not None
        assert row.content_sha256 == _sha256(content.decode("utf-8"))
        assert row.size_bytes == len(content)
        assert row.filename == "my-custom-style.csl"
        assert row.default_locale == "zh-CN"

    async def test_delete_unknown_raises_not_found(self, db_session: Any, tmp_path: Path) -> None:
        from app.core.exceptions import NotFoundException

        service = CitationStyleService(db=db_session, storage_root=tmp_path / "csl_styles")

        with pytest.raises(NotFoundException):
            await service.delete(USER_A, "no-such-style")

    async def test_delete_preset_raises_validation(self, db_session: Any, tmp_path: Path) -> None:
        service = CitationStyleService(db=db_session, storage_root=tmp_path / "csl_styles")

        with pytest.raises(ValidationException):
            await service.delete(USER_A, "apa")

    async def test_delete_missing_file_is_tolerated(self, db_session: Any, tmp_path: Path) -> None:
        """DB 行存在但磁盘文件缺失：删除不得报错。"""
        service = CitationStyleService(db=db_session, storage_root=tmp_path / "csl_styles")
        await service.upload(USER_A, filename="a.csl", content=make_csl().encode("utf-8"))
        (tmp_path / "csl_styles" / str(USER_A) / "my-custom-style.csl").unlink()

        await service.delete(USER_A, "my-custom-style")

        assert (await service.list_all(USER_A)).styles

    def test_storage_root_is_sibling_of_artifact_root(self, tmp_path: Path) -> None:
        """存储根 = artifact_root 同级目录（计划 §1.2），不与其他产物混用。"""
        from app.core.config import get_settings

        artifact_root = Path(get_settings().app.artifact_root)
        assert get_csl_storage_root() == (artifact_root.parent / "csl_styles").resolve()


# ===== 样式预览（2026-10-26 样式页重设计）=====


class TestStylePreviews:
    """`GET /citation-styles/previews`：每个样式一条**真实渲染**样例。

    ## 为什么必须加这个端点

    实测（服务器 + 真实浏览器，2026-10-26）：样式页 6 个样式**只有名字和
    `citation-number · citation-order · zh-CN` 这类元信息**，页面上
    `HAS_PREVIEW_WORD=false`、可点元素只有导航 + 上传按钮。

    后果：用户选样式时**看不到成品长什么样**。而 `gbt7714-2025-numeric` 与
    `gbt7714-2015-numeric` 的 numbering/ordering/locale **完全相同**
    （实测 `META_DISTINCT = 5/6`），光看元信息根本分不出区别。

    ## 为什么在后端渲染

    渲染必须走 citeproc + 真实 `.csl`。前端重写一套格式化器 = 第二份 CSL 实现，
    必然与导出结果不一致（导出走的是 `CitationRenderer`）。用户会看到
    「预览是这样、下载出来不是」——比没有预览更糟。
    """

    def test_requires_auth(self, style_env: StyleEnv) -> None:
        resp = style_env.client.get(f"{API}/previews")
        assert resp.status_code == 401

    def test_returns_one_preview_per_style(self, style_env: StyleEnv) -> None:
        """每个样式都要有预览，且 `style_id` 与列表接口一一对应。"""
        listed = style_env.client.get(API, headers=_auth(TOKEN_A)).json()
        resp = style_env.client.get(f"{API}/previews", headers=_auth(TOKEN_A))

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert set(body) >= {"previews", "sample_count"}

        preview_ids = [item["style_id"] for item in body["previews"]]
        assert preview_ids == [
            item["style_id"] for item in listed["styles"]
        ], "预览顺序与列表一致（前端按下标对齐，不靠二次查找）"

    def test_preview_contains_rendered_entries(self, style_env: StyleEnv) -> None:
        """预览必须是**渲染结果**，不是样式名或元信息回显。"""
        resp = style_env.client.get(f"{API}/previews", headers=_auth(TOKEN_A))
        body = resp.json()

        for item in body["previews"]:
            assert item["entries"], f"{item['style_id']} 预览为空"
            # 每条都应该是「作者/题名/出处」级别的内容，而不是 id 或元信息
            joined = " ".join(item["entries"])
            assert (
                "检索增强生成" in joined or "Ragas" in joined
            ), f"{item['style_id']} 的预览里没有样例文献内容：{joined[:120]}"

    def test_previews_are_distinct_across_styles(self, style_env: StyleEnv) -> None:
        """**核心断言**：6 个样式的输出必须互不相同。

        这是「6 种样式」这个说法的唯一证据。实测元信息只有 5 种不同组合，
        若渲染也相同，那这 6 个样式对用户就是同一样式。
        """
        resp = style_env.client.get(f"{API}/previews", headers=_auth(TOKEN_A))
        body = resp.json()

        rendered = {item["style_id"]: tuple(item["entries"]) for item in body["previews"]}
        distinct = {entries for entries in rendered.values()}
        assert len(distinct) == len(rendered), (
            "不同样式的预览输出重复了，说明渲染没真正生效："
            f"{ {k: v for k, v in rendered.items()} }"
        )

    def test_previews_have_no_fallback_warnings(self, style_env: StyleEnv) -> None:
        """预览不得带「已回退内置极简格式化器」告警。

        `CitationRenderer` 在 citeproc 失败时会**静默回退**到极简格式化器并记
        warning。回退后的输出看起来"成功"，但所有样式会长得一样。
        预览必须把 warning 透出来，否则前端会把兜底文本当成真样式展示。
        """
        resp = style_env.client.get(f"{API}/previews", headers=_auth(TOKEN_A))
        body = resp.json()

        for item in body["previews"]:
            assert not item["warnings"], f"{item['style_id']} 渲染走了兜底路径：{item['warnings']}"

    def test_previews_are_public_to_any_authenticated_user(self, style_env: StyleEnv) -> None:
        """预览不依赖用户自定义样式：任何登录用户看到同样的 6 个预置预览。"""
        resp_a = style_env.client.get(f"{API}/previews", headers=_auth(TOKEN_A))
        resp_b = style_env.client.get(f"{API}/previews", headers=_auth(TOKEN_B))

        ids_a = [item["style_id"] for item in resp_a.json()["previews"]]
        ids_b = [item["style_id"] for item in resp_b.json()["previews"]]
        assert ids_a == ids_b
