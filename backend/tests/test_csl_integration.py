"""CSL 渲染与导出链路的集成测试（Issue #4 M1 / 计划 §1.5）。

覆盖：
- `LiteratureExporter` 在给出 `citation_style` 时用 CSL 渲染器产出参考文献（渲染期生效）；
- 正文既有的 `## 参考文献` 段落被替换，Issue #3 的 `## 引用核验` 段落保留；
- `[E{n}]` 证据标记**不受影响**（验收要求）；
- `[[cite:ref_id]]` 占位被替换为样式对应标记；
- **不给 `citation_style` 时输出与既有版本逐字一致**（向后兼容）；
- 未知样式给出明确错误；
- 任务配置确实携带 `citation_style`（端到端接线的前提）。
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.exceptions import ValidationException
from app.orchestration.contracts import LiteratureReviewRequest, ReviewReport
from app.tools.literature.exporter import LiteratureExporter

BODY = """## 摘要
本文综述检索增强生成的评估方法 [E1]，并比较多种评测框架 [E2]。

## 参考文献
- [1] 旧的手写参考文献行 — https://example.org/old
"""


def _bibliography() -> list[dict]:
    """reviewer 产出的参考文献条目（含 Issue #4 新增的 authors/year/language）。"""
    return [
        {
            "index": 1,
            "title": "Ragas: Automated Evaluation of Retrieval Augmented Generation",
            "source_url": "https://arxiv.org/abs/2309.15217v2",
            "source_id": "2309.15217v2",
            "authors": ["Shahul Es", "Jithin James", "Luis Espinosa-Anke", "Steven Schockaert"],
            "year": 2023,
            "language": "en-US",
            "verification_status": "verified",
        },
        {
            "index": 2,
            "title": "面向学位论文的中文参考文献著录规则研究",
            "source_url": "https://doi.org/10.1234/zh.journal.2024.001",
            "source_id": "zh.journal.2024.001",
            "authors": ["牛耕田", "李泽", "张三", "王五"],
            "year": 2024,
            "language": "zh-CN",
            "verification_status": "verified",
        },
    ]


def _report(*, body: str = BODY) -> ReviewReport:
    return ReviewReport(
        final_markdown=body,
        bibliography=_bibliography(),
        scores={"citation_traceability": 0.95, "quality": 0.9},
        issues=[],
        stats={"title": "检索增强生成评估方法综述"},
    )


def _export(report: ReviewReport, style: str | None, tmp_path) -> str:
    exporter = LiteratureExporter(tmp_path)
    user_id, task_id = uuid4(), uuid4()
    kwargs = {} if style is None else {"citation_style": style}
    artifact = exporter.export_markdown(user_id, task_id, report, **kwargs)
    return exporter.resolve_for_user(user_id, task_id, artifact.artifact_id).read_text(
        encoding="utf-8"
    )


class TestBackwardCompatibility:
    def test_without_citation_style_output_is_legacy(self, tmp_path) -> None:
        """不给样式时保持既有 `_bibliography_line` 行为（老任务/老测试不受影响）。"""
        text = _export(_report(), None, tmp_path)

        assert "旧的手写参考文献行" in text
        assert "（核验：" not in text
        assert "[J]" not in text

    def test_no_placeholder_and_no_style_is_identity(self, tmp_path) -> None:
        text = _export(_report(body="# 标题\n\n正文 [E1]。\n"), None, tmp_path)
        assert "[[cite:" not in text
        assert "[E1]" in text


class TestCslRenderingIsApplied:
    def test_bibliography_is_rendered_by_csl(self, tmp_path) -> None:
        text = _export(_report(), "gbt7714", tmp_path)

        # Issue #4 验收：GB/T 7714—2025 为默认，且是确定性渲染（不再是手写行）
        assert "旧的手写参考文献行" not in text
        assert "## 参考文献" in text
        # 逐条 locale 生效：中文条目「等」、英文条目 "et al."
        assert "et al." in text
        assert "等" in text
        # 文献类型标识（GB/T 7714 特征；带 URL 的期刊文献为 [J/OL]）
        assert "[J" in text

    def test_alias_and_explicit_id_agree(self, tmp_path) -> None:
        alias = _export(_report(), "gbt7714", tmp_path)
        explicit = _export(_report(), "gbt7714-2025-numeric", tmp_path)
        assert alias == explicit

    def test_evidence_markers_are_untouched(self, tmp_path) -> None:
        """验收项：`[E{n}]` 证据溯源不受引用样式影响。"""
        text = _export(_report(), "gbt7714", tmp_path)
        assert "[E1]" in text
        assert "[E2]" in text

    def test_reference_verification_section_is_preserved(self, tmp_path) -> None:
        """Issue #3 的 `## 引用核验` 段落必须保留（两个特性叠加）。"""
        body = (
            BODY
            + "\n## 引用核验\n引用核验：已验证 2 条 / 元数据不符 0 条 / 未找到 0 条 / 未核验 0 条；拦截开关：关闭\n"
        )
        text = _export(_report(body=body), "gbt7714", tmp_path)

        assert "## 引用核验" in text
        assert "已验证 2 条" in text
        assert "## 参考文献" in text

    def test_inline_placeholders_are_substituted(self, tmp_path) -> None:
        body = BODY.replace("[E1]", "[[cite:src:2309.15217v2]]")
        text = _export(_report(body=body), "gbt7714", tmp_path)

        assert "[[cite:" not in text
        assert "[1]" in text

    def test_inline_placeholder_supports_doi_ref_id(self, tmp_path) -> None:
        """裁决 R-M1-2：DOI 型 ref_id 含 `/`，占位符必须能匹配并替换。"""
        body = BODY.replace("[E2]", "[[cite:doi:10.1234/zh.journal.2024.001]]")
        text = _export(_report(body=body), "gbt7714", tmp_path)

        assert "[[cite:" not in text
        assert "doi:10.1234" not in text

    def test_unknown_style_is_rejected(self, tmp_path) -> None:
        with pytest.raises(ValidationException):
            _export(_report(), "does-not-exist-style", tmp_path)


class TestStyleSwitching:
    """Issue #4 头号验收项：同一篇综述切换 4 种以上样式，参考文献正确重排且 [E{n}] 不受影响。"""

    STYLES = ("gbt7714", "gbt7714-2015-numeric", "apa", "ieee", "nature")

    def test_five_styles_render_differently_and_keep_evidence_markers(self, tmp_path) -> None:
        report = _report()
        outputs = {style: _export(report, style, tmp_path) for style in self.STYLES}

        # ① 五种样式产出互不相同（说明样式真的生效，而不是同一份手写行）
        assert len(set(outputs.values())) == len(self.STYLES)
        # ② 每种都保留 [E{n}] 证据标记，且都替换掉了旧的手写参考文献
        for style, text in outputs.items():
            assert "[E1]" in text and "[E2]" in text, style
            assert "旧的手写参考文献行" not in text, style
            assert "## 参考文献" in text, style
        # ③ 编号制式差异：顺序编码制有条目编号，APA 用著者-出版年（无 [n] 前缀）
        for style in ("gbt7714", "ieee", "nature"):
            assert "[1]" in outputs[style], style
        assert "[1]" not in outputs["apa"]


class TestTaskConfigCarriesStyle:
    def test_request_keeps_citation_style_for_wiring(self) -> None:
        """端到端接线前提：样式必须落在任务配置里，供渲染期读取。"""
        request = LiteratureReviewRequest(
            topic="检索增强生成评估综述", keywords=["RAG"], citation_style="ieee"
        )
        assert request.model_dump(mode="json")["citation_style"] == "ieee"


class TestBibliographyEnrichment:
    """复盘必须为参考文献补齐 CSL 元数据（否则渲染只能得到「摘录当题名」）。"""

    async def test_evidence_entry_is_upgraded_from_artifact(self) -> None:
        from app.orchestration.agents.reviewer import ReviewerAgent

        url = "https://arxiv.org/abs/2309.15217v2"
        payload = {
            "trace_id": "t-csl",
            "topic": "检索增强生成评估方法",
            "results": [
                {
                    "node_id": "n1",
                    "content": "正文 [E1]",
                    "evidence": [
                        {
                            "doc_id": str(uuid4()),
                            "chunk_id": str(uuid4()),
                            "source_url": url,
                            "excerpt": "这段是证据摘录，不应该出现在参考文献题名里",
                        }
                    ],
                    "artifacts": [
                        {
                            "source_id": "2309.15217v2",
                            "title": "Ragas: Automated Evaluation of Retrieval Augmented Generation",
                            "authors": ["Shahul Es", "Jithin James", "Luis Espinosa-Anke"],
                            "published_at": "2023-09-26T19:23:54Z",
                            "source_url": url,
                            "pdf_url": "https://arxiv.org/pdf/2309.15217v2",
                            "categories": ["cs.CL"],
                        }
                    ],
                    "duration_ms": 1,
                }
            ],
            "validations": [],
            "event_stats": {},
        }

        report = await ReviewerAgent().execute(payload)
        entry = report["bibliography"][0]

        assert entry["title"].startswith("Ragas:")
        assert entry["authors"] == ["Shahul Es", "Jithin James", "Luis Espinosa-Anke"]
        assert entry["year"] == 2023
        assert entry["language"] == "en-US"

    async def test_chinese_artifact_is_tagged_zh_cn(self) -> None:
        from app.orchestration.agents.reviewer import ReviewerAgent

        payload = {
            "trace_id": "t-csl-zh",
            "topic": "中文文献",
            "results": [
                {
                    "node_id": "n1",
                    "content": "正文",
                    "evidence": [],
                    "artifacts": [
                        {
                            "source_id": "zh-1",
                            "title": "面向学位论文的中文参考文献著录规则研究",
                            "authors": ["牛耕田", "李泽"],
                            "published_at": "2024-03-01T00:00:00Z",
                            "source_url": "https://doi.org/10.1234/zh.journal.2024.001",
                        }
                    ],
                    "duration_ms": 1,
                }
            ],
            "validations": [],
            "event_stats": {},
        }

        report = await ReviewerAgent().execute(payload)
        entry = report["bibliography"][0]

        assert entry["language"] == "zh-CN"
        assert entry["authors"] == ["牛耕田", "李泽"]
        assert entry["year"] == 2024
