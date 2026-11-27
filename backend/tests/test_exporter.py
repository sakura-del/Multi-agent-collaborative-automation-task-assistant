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
import re
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
from app.tools.literature.exporter import LiteratureExporter, prepare_template_export
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


class TestExportNormalizationAndDocxSafety:
    """9 项质量缺陷回归拦截：P0-2/3/4/5 与 P1-2/4。"""

    def test_docx_export_strips_internal_markers_html_and_model_infer(self, tmp_path: Path) -> None:
        dirty_md = """# 边缘计算综述

## 摘要
摘要不应该带任何引用角标 [E1][E2]。

## 方法与结果
现有架构显著降低延迟 [E1][E2][E3]。模型推断：仍需协处理器辅助。

## 参考文献
- [1] Dirty Title. — https://example.org/1
"""
        report = _report(
            final_markdown=dirty_md,
            bibliography=[
                {
                    "index": 1,
                    "title": "Clean Study Title.",
                    "authors": ["<p>Luo Shunan<sup>1</sup>, Deng Yun<sup>1</sup></p>"],
                    "year": 2025,
                    "source_url": "https://doi.org/10.1000/1",
                },
                {
                    "index": 2,
                    "title": "Second Paper",
                    "authors": ["Alice"],
                    "year": 2024,
                    "source_url": "https://doi.org/10.1000/2",
                },
                {
                    "index": 3,
                    "title": "Third Paper",
                    "authors": ["Bob"],
                    "year": 2023,
                    "source_url": "https://doi.org/10.1000/3",
                },
            ],
        )
        exporter = LiteratureExporter(tmp_path / "artifacts")
        artifact = exporter.export_docx(USER_A, TASK_ID, report)
        target = exporter.resolve_for_user(USER_A, TASK_ID, artifact.artifact_id)
        doc = Document(str(target))
        joined = "\n".join(p.text for p in doc.paragraphs)

        assert not re.search(r"\[E\d+\]", joined)
        assert "模型推断：" not in joined
        assert "<p>" not in joined and "<sup>" not in joined
        assert "[1-3]" in joined
        # 摘要不含数字角标
        abstract_line = next(p.text for p in doc.paragraphs if "摘要不应该带任何引用角标" in p.text)
        assert "[1" not in abstract_line
        assert joined.count("参考文献") == 1

    def test_prepare_template_export_dedups_doc_id_html_double_period_and_numbers_headings(
        self,
    ) -> None:
        raw_body = """# 基于STM32的手环研究

## 摘要
基于STM32平台实现了可穿戴健康监测 [E1][E2][E3]。

## STM32选型依据
多项研究采用了该系列MCU [E1][E2][E3]，并结合知识库文档展开分析 [E4][E5]。

### 传感器硬件架构
采用低功耗传感器与DMA采样 [E4]。

## 参考文献
- [1] 旧的自写参考文献列表

## 引用核验
引用核验：已验证 3 条
"""
        shared_doc_id = str(uuid4())
        entries = [
            {
                "index": 1,
                "title": "First Paper.",
                "authors": [
                    "<p>Luo Shunan<sup>1</sup>, Deng Yun<sup>1</sup>, Li Ming<sup>2</sup></p>"
                ],
                "year": 2025,
                "source_url": "https://doi.org/10.1000/a",
            },
            {
                "index": 2,
                "title": "Second Paper。",
                "authors": ["Bob"],
                "year": 2024,
                "source_url": "https://doi.org/10.1000/b",
            },
            {
                "index": 3,
                "title": "Third Paper",
                "authors": ["Carol"],
                "year": 2023,
                "source_url": "https://doi.org/10.1000/c",
            },
            {
                "index": 4,
                "title": "chunk fragment one that should be replaced",
                "doc_id": shared_doc_id,
            },
            {
                "index": 5,
                "title": "chunk fragment two from same pdf",
                "doc_id": shared_doc_id,
            },
        ]
        cleaned_body, clean_bib, clean_entries = prepare_template_export(
            raw_body,
            entries,
            doc_titles_by_id={shared_doc_id: "Factually Tactile Wearable Survey."},
        )
        # 首行 # 题目与 ## 参考文献 / ## 引用核验 被剥离
        assert not cleaned_body.startswith("# 基于STM32")
        assert "## 参考文献" not in cleaned_body
        assert "## 引用核验" not in cleaned_body
        # 摘要无引文角标
        assert "可穿戴健康监测。" in cleaned_body
        # [E1][E2][E3] 合并压缩为 [1-3]；[E4][E5] 因同 doc_id 归并重映射为 [4]
        assert "[1-3]" in cleaned_body
        assert "[4]" in cleaned_body
        assert not re.search(r"\[E\d+\]", cleaned_body)
        # 标题补全分级序号
        assert "## 1. STM32选型依据" in cleaned_body
        assert "### 1.1 传感器硬件架构" in cleaned_body
        # 同 doc_id 仅保留 1 条文献，共 4 条参考文献；无 HTML 与末尾双句号
        assert len(clean_entries) == 4
        assert len(clean_bib) == 4
        bib_joined = "\n".join(clean_bib)
        assert "<p>" not in bib_joined and "<sup>" not in bib_joined
        assert ".." not in bib_joined
        assert "Factually Tactile Wearable Survey" in bib_joined


class TestIssue72ExportPolish:
    """Issue #72 Fix 1/2/6：摘要不重复、空局限性剔除、参考文献兜底补齐。"""

    def test_abstract_is_not_copied_from_body_when_stats_missing(self, tmp_path: Path) -> None:
        body = "## 问题提出的背景\n\n本文研究面向运动手环的低功耗心率监测系统 [1]。"
        report = _report(
            final_markdown=body,
            stats={"title": "低功耗心率监测系统研究"},
            bibliography=[{"index": 1, "title": "Wearable HR Survey", "year": 2024}],
        )
        exporter = LiteratureExporter(tmp_path / "artifacts")
        artifact = exporter.export_markdown(USER_A, TASK_ID, report)
        text = exporter.resolve_for_user(USER_A, TASK_ID, artifact.artifact_id).read_text(
            encoding="utf-8"
        )

        assert "## 摘要" not in text
        assert text.count("本文研究面向运动手环的低功耗心率监测系统") == 1

    def test_empty_limitations_section_is_removed(self, tmp_path: Path) -> None:
        report = _report(
            final_markdown="## 方法\n\n正文 [1]。",
            stats={"title": "方法研究", "abstract": "摘要正文。", "limitations": ["（无）"]},
            bibliography=[{"index": 1, "title": "Methods", "year": 2024}],
        )
        exporter = LiteratureExporter(tmp_path / "artifacts")
        artifact = exporter.export_markdown(USER_A, TASK_ID, report)
        text = exporter.resolve_for_user(USER_A, TASK_ID, artifact.artifact_id).read_text(
            encoding="utf-8"
        )

        assert "局限性" not in text
        assert "（无）" not in text

    def test_bibliography_is_backfilled_when_section_missing(self, tmp_path: Path) -> None:
        body = "## 方法\n\n我们比较了检索质量 [1] 与证据可追溯性 [2]。"
        report = _report(
            final_markdown=body,
            stats={"title": "方法研究", "abstract": "摘要正文。"},
            bibliography=[
                {"index": 1, "title": "RAG Evaluation Survey", "year": 2024},
                {"index": 2, "title": "Evidence Tracing", "year": 2023},
            ],
        )
        exporter = LiteratureExporter(tmp_path / "artifacts")
        artifact = exporter.export_markdown(USER_A, TASK_ID, report)
        text = exporter.resolve_for_user(USER_A, TASK_ID, artifact.artifact_id).read_text(
            encoding="utf-8"
        )

        assert "## 参考文献" in text
        assert "RAG Evaluation Survey" in text
        assert "Evidence Tracing" in text

    def test_adjacent_duplicate_paragraphs_are_deduped(self, tmp_path: Path) -> None:
        duplicated = "## 方法\n\n同一段重复文字。\n\n同一段重复文字。\n"
        report = _report(
            final_markdown=duplicated,
            stats={"title": "方法研究", "abstract": "摘要正文。"},
            bibliography=[{"index": 1, "title": "Methods", "year": 2024}],
        )
        exporter = LiteratureExporter(tmp_path / "artifacts")
        artifact = exporter.export_markdown(USER_A, TASK_ID, report)
        text = exporter.resolve_for_user(USER_A, TASK_ID, artifact.artifact_id).read_text(
            encoding="utf-8"
        )

        assert text.count("同一段重复文字") == 1
