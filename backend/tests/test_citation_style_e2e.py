"""Issue #4 M2 端到端验收：上传 `.csl` → 用它跑任务 → 产物按该样式渲染。

这是 M2 的核心验收项（Issue #4 原文「上传 `.csl` 后可作为可选样式」）。
前三个工作流各自覆盖了自己的层，本文件把**上传 → 解析 → 渲染 → 导出**串起来。
"""

from __future__ import annotations

from uuid import uuid4

from app.api.v1.endpoints.citation_styles import get_csl_storage_root
from app.knowledge.services.citation_style_service import CitationStyleService
from app.orchestration.contracts import ReviewReport
from app.tools.csl.styles import StyleRegistry
from app.tools.literature.exporter import LiteratureExporter

#: 极简自定义样式：只输出「作者. 题名 (年份)」，用于证明样式真的被采用
CUSTOM_STYLE = """<?xml version="1.0" encoding="utf-8"?>
<style xmlns="http://purl.org/net/xbiblio/csl" class="in-text" version="1.0"
       default-locale="en-US">
  <info>
    <title>实验室极简样式</title>
    <id>https://example.org/styles/lab-minimal</id>
  </info>
  <citation>
    <layout>
      <text variable="title"/>
    </layout>
  </citation>
  <bibliography>
    <layout>
      <names variable="author">
        <name name-as-sort-order="all" and="text"/>
        <label prefix=", "/>
      </names>
      <text variable="title" prefix=". "/>
      <date variable="issued" prefix=" (" suffix=")">
        <date-part name="year"/>
      </date>
    </layout>
  </bibliography>
</style>
"""

BIBLIOGRAPHY = [
    {
        "index": 1,
        "title": "Ragas: Automated Evaluation of Retrieval Augmented Generation",
        "source_url": "https://arxiv.org/abs/2309.15217v2",
        "authors": ["Shahul Es", "Jithin James"],
        "year": 2023,
        "language": "en-US",
    }
]


def _report() -> ReviewReport:
    return ReviewReport(
        final_markdown="## 摘要\n本文综述评估方法 [E1]。\n",
        bibliography=[dict(entry) for entry in BIBLIOGRAPHY],
        scores={"quality": 0.9},
        issues=[],
        stats={"title": "自定义样式端到端验收"},
    )


async def _upload_and_render(db: object, tmp_path) -> str:
    """上传样式 → 登记到注册表 → 用该样式导出 Markdown，返回导出正文。"""
    storage_root = tmp_path / "csl_styles"
    registry = StyleRegistry()
    service = CitationStyleService(db, storage_root, registry=registry)  # type: ignore[arg-type]

    user_id = uuid4()
    style = await service.upload(
        user_id,
        filename="lab-minimal.csl",
        content=CUSTOM_STYLE.encode("utf-8"),
    )

    # 上传后注册表应已登记该样式（渲染期可解析）
    assert registry.resolve(style.style_id) == style.style_id

    # 导出器必须注入**同一**注册表，否则渲染期看不到自定义样式
    exporter = LiteratureExporter(tmp_path / "artifacts", style_resolver=registry)
    task_id = uuid4()
    artifact = exporter.export_markdown(user_id, task_id, _report(), citation_style=style.style_id)
    return exporter.resolve_for_user(user_id, task_id, artifact.artifact_id).read_text(
        encoding="utf-8"
    )


class TestUploadedStyleIsUsable:
    async def test_uploaded_style_renders_the_review(self, tmp_path) -> None:
        """M2 头号验收：上传的样式能真正用于渲染（而非只出现在列表里）。"""
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        import app.db.models  # noqa: F401  注册全部 ORM
        from app.db.base import Base

        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'm2-e2e.db'}")
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        try:
            async with session_factory() as session:
                markdown = await _upload_and_render(session, tmp_path)
        finally:
            await engine.dispose()

        # 自定义样式生效：参考文献按该样式渲染，且不再出现 GB/T 的类型标识
        assert "## 参考文献" in markdown
        assert "Ragas: Automated Evaluation of Retrieval Augmented Generation" in markdown
        assert "[J" not in markdown
        # 默认样式仍是 GB/T 2025（未被上传样式顶替）
        assert StyleRegistry().meta("gbt7714").style_id == "gbt7714-2025-numeric"

    async def test_storage_root_is_under_csl_styles(self, tmp_path, monkeypatch) -> None:
        """落盘位置与计划 §1.2 一致：`artifact_root.parent/csl_styles`。"""
        from app.core.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(
            settings.app, "artifact_root", str(tmp_path / "artifacts"), raising=False
        )

        root = get_csl_storage_root()

        assert root.name == "csl_styles"
        assert root.parent == tmp_path
