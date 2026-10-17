"""文献导出与复盘汇总测试（Plan 3 Task 5）。

覆盖：
- Markdown 导出包含标题/摘要/正文章节/局限性/参考文献，并写入 sha256 与体积；
- HTML 转义、非 HTTP(S) 链接拒绝；
- DOCX 可被 python-docx 打开且标题层级一致；
- `resolve_for_user()` 只能解析服务端生成的 artifact_id，路径穿越与跨用户一律 404；
- ReviewerAgent 只基于已持久化结果组装 `ReviewReport`（不调用外部工具）。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from docx import Document

from app.core.exceptions import NotFoundException, ValidationException
from app.orchestration.agents.reviewer import ReviewerAgent
from app.orchestration.contracts import (
    Evidence,
    ExecutionResult,
    ValidationCheck,
    ValidationReport,
)
from app.tools.literature.exporter import LiteratureExporter
from app.tools.literature.models import Artifact

USER_A = UUID("44444444-4444-4444-4444-444444444444")
USER_B = UUID("55555555-5555-5555-5555-555555555555")
TASK_ID = UUID("66666666-6666-6666-6666-666666666666")

MARKDOWN_BODY = """# 检索增强生成的评估方法

## 摘要
本文综述检索增强生成的评估方法 [E1]。

## 方法
我们比较了检索质量与证据可追溯性指标 [E2]。

## 局限性
- 离线语料仅 10 篇，覆盖范围有限。

## 参考文献
- [1] RAG Evaluation Survey — https://arxiv.org/abs/2401.12341v1
- [2] Evidence Tracing — https://arxiv.org/abs/2401.12342v1
"""


def _report(**overrides):
    from app.orchestration.contracts import ReviewReport

    payload = {
        "final_markdown": MARKDOWN_BODY,
        "bibliography": [
            {
                "index": 1,
                "title": "RAG Evaluation Survey",
                "source_url": "https://arxiv.org/abs/2401.12341v1",
                "source_id": "2401.12341v1",
            },
            {
                "index": 2,
                "title": "Evidence Tracing",
                "source_url": "https://arxiv.org/abs/2401.12342v1",
                "source_id": "2401.12342v1",
            },
        ],
        "scores": {"citation_traceability": 1.0, "quality": 0.92},
        "issues": [],
        "stats": {
            "node_count": 5,
            "event_count": 30,
            "total_tokens": 1234,
            "title": "检索增强生成的评估方法",
            "abstract": "本文综述检索增强生成的评估方法 [E1]。",
            "limitations": ["离线语料仅 10 篇，覆盖范围有限。"],
        },
    }
    payload.update(overrides)
    return ReviewReport.model_validate(payload)


class TestMarkdownExport:
    def test_writes_all_required_parts_and_hashes(self, tmp_path: Path) -> None:
        exporter = LiteratureExporter(tmp_path / "artifacts")
        artifact = exporter.export_markdown(USER_A, TASK_ID, _report())

        assert isinstance(artifact, Artifact)
        assert artifact.kind == "markdown"
        assert artifact.filename.endswith(".md")
        target = exporter.resolve_for_user(USER_A, TASK_ID, artifact.artifact_id)
        assert target == tmp_path / "artifacts" / str(USER_A) / str(TASK_ID) / artifact.filename

        text = target.read_text(encoding="utf-8")
        for required in ("检索增强生成的评估方法", "摘要", "方法", "局限性", "参考文献"):
            assert required in text
        assert "https://arxiv.org/abs/2401.12341v1" in text

        raw = target.read_bytes()
        assert artifact.size == len(raw)
        assert artifact.sha256 == hashlib.sha256(raw).hexdigest()
        assert artifact.created_at.tzinfo is not None

    def test_escapes_raw_html(self, tmp_path: Path) -> None:
        report = _report(
            final_markdown=MARKDOWN_BODY.replace(
                "本文综述检索增强生成的评估方法 [E1]。",
                "<script>alert(1)</script> 综述 [E1]。",
            )
        )
        exporter = LiteratureExporter(tmp_path / "artifacts")
        artifact = exporter.export_markdown(USER_A, TASK_ID, report)
        text = exporter.resolve_for_user(USER_A, TASK_ID, artifact.artifact_id).read_text(
            encoding="utf-8"
        )

        assert "<script>" not in text
        assert "&lt;script&gt;" in text

    @pytest.mark.parametrize(
        "bad_link",
        [
            "[x](javascript:alert(1))",
            "[x](file:///etc/passwd)",
            "[x](data:text/html;base64,PHNjcmlwdD4=)",
        ],
    )
    def test_rejects_unsafe_links(self, tmp_path: Path, bad_link: str) -> None:
        exporter = LiteratureExporter(tmp_path / "artifacts")
        report = _report(final_markdown=f"{MARKDOWN_BODY}\n{bad_link}\n")
        with pytest.raises(ValidationException):
            exporter.export_markdown(USER_A, TASK_ID, report)

    def test_escapes_inline_event_handler_html(self, tmp_path: Path) -> None:
        exporter = LiteratureExporter(tmp_path / "artifacts")
        report = _report(final_markdown=f"{MARKDOWN_BODY}\n<img src=x onerror=alert(1)>\n")
        artifact = exporter.export_markdown(USER_A, TASK_ID, report)
        text = exporter.resolve_for_user(USER_A, TASK_ID, artifact.artifact_id).read_text(
            encoding="utf-8"
        )
        assert "<img" not in text
        assert "&lt;img" in text

    def test_accepts_http_and_https_links(self, tmp_path: Path) -> None:
        exporter = LiteratureExporter(tmp_path / "artifacts")
        report = _report(
            final_markdown=MARKDOWN_BODY
            + "\n[官网](http://example.com/a) 与 https://arxiv.org/abs/1\n"
        )
        artifact = exporter.export_markdown(USER_A, TASK_ID, report)
        assert artifact.size > 0


class TestDocxExport:
    def test_writes_docx_with_same_headings(self, tmp_path: Path) -> None:
        exporter = LiteratureExporter(tmp_path / "artifacts")
        artifact = exporter.export_docx(USER_A, TASK_ID, _report())

        assert artifact.kind == "docx"
        assert artifact.filename.endswith(".docx")
        target = exporter.resolve_for_user(USER_A, TASK_ID, artifact.artifact_id)

        document = Document(str(target))
        texts = [paragraph.text for paragraph in document.paragraphs]
        joined = "\n".join(texts)
        for required in ("检索增强生成的评估方法", "摘要", "局限性", "参考文献"):
            assert required in joined
        headings = [
            paragraph.text
            for paragraph in document.paragraphs
            if paragraph.style.name.startswith("Heading")
        ]
        assert "摘要" in headings

    def test_markdown_and_docx_have_distinct_ids(self, tmp_path: Path) -> None:
        exporter = LiteratureExporter(tmp_path / "artifacts")
        markdown = exporter.export_markdown(USER_A, TASK_ID, _report())
        docx = exporter.export_docx(USER_A, TASK_ID, _report())
        assert markdown.artifact_id != docx.artifact_id


class TestArtifactResolution:
    def test_rejects_traversal_and_unknown_ids(self, tmp_path: Path) -> None:
        exporter = LiteratureExporter(tmp_path / "artifacts")
        exporter.export_markdown(USER_A, TASK_ID, _report())

        for bad_id in ("../../etc/passwd", "..%2f..%2fetc", "not-hex", "a" * 31):
            with pytest.raises(NotFoundException):
                exporter.resolve_for_user(USER_A, TASK_ID, bad_id)

    def test_other_user_cannot_resolve(self, tmp_path: Path) -> None:
        exporter = LiteratureExporter(tmp_path / "artifacts")
        artifact = exporter.export_markdown(USER_A, TASK_ID, _report())

        with pytest.raises(NotFoundException):
            exporter.resolve_for_user(USER_B, TASK_ID, artifact.artifact_id)

    def test_other_task_cannot_resolve(self, tmp_path: Path) -> None:
        exporter = LiteratureExporter(tmp_path / "artifacts")
        artifact = exporter.export_markdown(USER_A, TASK_ID, _report())

        with pytest.raises(NotFoundException):
            exporter.resolve_for_user(USER_A, uuid4(), artifact.artifact_id)


class TestReviewerAssembly:
    """ReviewerAgent 只消费已持久化结果，不调用 arXiv/RAG/文件工具。"""

    def _results(self) -> list[ExecutionResult]:
        return [
            ExecutionResult(
                node_id="n3",
                content="摘要\n本文综述评估方法 [E1]。\n\n局限性\n- 语料有限。",
                evidence=[
                    Evidence(
                        doc_id=uuid4(),
                        chunk_id=uuid4(),
                        source_url="https://arxiv.org/abs/2401.12341v1",
                        excerpt="证据一",
                    )
                ],
                duration_ms=10,
                prompt_tokens=100,
                completion_tokens=50,
            ),
            ExecutionResult(node_id="n4", content="校验通过", duration_ms=1),
        ]

    def _validations(self) -> list[ValidationReport]:
        return [
            ValidationReport(
                passed=True,
                score=90.0,
                checks=[
                    ValidationCheck(name="citation_markers_resolve", passed=True, message="ok")
                ],
                retryable=False,
                feedback="",
            )
        ]

    async def test_assembles_review_report_from_persisted_results(self) -> None:
        agent = ReviewerAgent()
        report = await agent.execute(
            {
                "trace_id": "trace-1",
                "topic": "检索增强生成的评估方法",
                "results": [item.model_dump(mode="json") for item in self._results()],
                "validations": [item.model_dump(mode="json") for item in self._validations()],
                "event_stats": {"event_count": 12, "task_id": str(TASK_ID)},
            }
        )

        assert report["final_markdown"].startswith("# 检索增强生成的评估方法")
        assert "参考文献" in report["final_markdown"]
        assert "https://arxiv.org/abs/2401.12341v1" in report["final_markdown"]
        assert report["bibliography"][0]["source_url"] == "https://arxiv.org/abs/2401.12341v1"
        assert report["scores"]["citation_traceability"] == 0.9
        assert report["stats"]["event_count"] == 12
        assert report["stats"]["documents"] == 1

    async def test_requires_results(self) -> None:
        agent = ReviewerAgent()
        with pytest.raises(ValueError):
            await agent.execute({"trace_id": "t", "results": [], "topic": "x"})
