"""T5 导出核验标记与统计章节测试（计划 §2.6 后半段）。

覆盖：
- ``stats["reference_verification"]`` 存在且正文没有 ``## 引用核验`` → 追加统计章节
  （Markdown 与 DOCX 走同一条 ``_compose_document`` 路径）；
- 正文已有该章节时不重复追加（复盘产物已经带章节的场景）；
- 参考文献行带核验标记；无核验信息的老数据（历史 payload）渲染与现在完全一致；
- 既有 ``_validate_links`` 安全校验不被破坏。

本文件不产生任何网络请求。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from docx import Document

from app.core.exceptions import ValidationException
from app.orchestration.contracts import ReviewReport
from app.tools.literature.exporter import LiteratureExporter

USER_A = UUID("77777777-7777-7777-7777-777777777777")
TASK_ID = UUID("88888888-8888-8888-8888-888888888888")

TITLE = "检索增强生成的评估方法"

#: 正文故意不含 ``## 参考文献`` / ``## 引用核验``，以走导出侧的追加逻辑
BODY = f"# {TITLE}\n\n## 摘要\n本文综述检索增强生成的评估方法 [E1]。\n"

VERIFICATION_SUMMARY: dict[str, Any] = {
    "total": 2,
    "verified": 1,
    "metadata_mismatch": 1,
    "not_found": 0,
    "unverified": 0,
    "cached_hits": 0,
    "dropped": 1,
    "intercepted": True,
}

BIBLIOGRAPHY: list[dict[str, Any]] = [
    {
        "index": 1,
        "title": "RAG Evaluation Survey",
        "source_url": "https://doi.org/10.1234/verified",
        "source_id": "10.1234/verified",
        "verification_status": "verified",
        "verification_reason": "DOI 命中 crossref",
    },
    {
        "index": 2,
        "title": "Evidence Tracing",
        "source_url": "https://arxiv.org/abs/2401.12342v1",
        "source_id": "2401.12342v1",
        "verification_status": "not_found",
        "verification_reason": "标题检索无命中",
    },
]

EXPECTED_SECTION = (
    "## 引用核验\n"
    "引用核验：已验证 1 条 / 元数据不符 1 条 / 未找到 0 条 / 未核验 0 条 / 已拦截 1 条；"
    "拦截开关：开启"
)

VERIFIED_LINE = (
    "- [1] RAG Evaluation Survey (10.1234/verified) — https://doi.org/10.1234/verified"
    "（核验：已验证；撤稿状态未知）"
)
NOT_FOUND_LINE = (
    "- [2] Evidence Tracing (2401.12342v1) — https://arxiv.org/abs/2401.12342v1"
    "（核验：未找到；撤稿状态未知）"
)

#: 无核验信息时的历史渲染（老数据必须逐字一致）
LEGACY_LINES = (
    "- [1] RAG Evaluation Survey (10.1234/verified) — https://doi.org/10.1234/verified\n"
    "- [2] Evidence Tracing (2401.12342v1) — https://arxiv.org/abs/2401.12342v1"
)


def make_report(**overrides: Any) -> ReviewReport:
    """构造导出报告；默认带核验统计与核验标记。"""
    payload: dict[str, Any] = {
        "final_markdown": BODY,
        "bibliography": [dict(entry) for entry in BIBLIOGRAPHY],
        "scores": {"citation_traceability": 1.0, "quality": 0.9},
        "issues": [],
        "stats": {
            "title": TITLE,
            "abstract": "本文综述检索增强生成的评估方法 [E1]。",
            "limitations": ["离线语料覆盖有限。"],
            "reference_verification": dict(VERIFICATION_SUMMARY),
        },
    }
    payload.update(overrides)
    return ReviewReport.model_validate(payload)


def legacy_report(**overrides: Any) -> ReviewReport:
    """历史 payload：bibliography 无核验信息、stats 无核验统计。"""
    stats = {
        "title": TITLE,
        "abstract": "本文综述检索增强生成的评估方法 [E1]。",
        "limitations": ["离线语料覆盖有限。"],
    }
    stats.update(overrides.pop("stats", {}))
    payload: dict[str, Any] = {
        "final_markdown": BODY,
        "bibliography": [
            {key: value for key, value in entry.items() if not key.startswith("verification_")}
            for entry in BIBLIOGRAPHY
        ],
        "scores": {"citation_traceability": 1.0, "quality": 0.9},
        "issues": [],
        "stats": stats,
    }
    payload.update(overrides)
    return ReviewReport.model_validate(payload)


def exported_text(tmp_path: Path, report: ReviewReport) -> str:
    """导出 Markdown 并读回文本。"""
    exporter = LiteratureExporter(tmp_path / "artifacts")
    artifact = exporter.export_markdown(USER_A, TASK_ID, report)
    return exporter.resolve_for_user(USER_A, TASK_ID, artifact.artifact_id).read_text(
        encoding="utf-8"
    )


class TestMarkdownVerificationSection:
    def test_appends_verification_section(self, tmp_path: Path) -> None:
        text = exported_text(tmp_path, make_report())

        assert EXPECTED_SECTION in text
        # 参考文献与统计章节并存（正文原本没有这两章）
        assert "## 参考文献" in text
        assert VERIFIED_LINE in text
        assert NOT_FOUND_LINE in text

    def test_does_not_duplicate_existing_section(self, tmp_path: Path) -> None:
        body = f"{BODY}\n## 参考文献\n{VERIFIED_LINE}\n\n{EXPECTED_SECTION}\n"
        text = exported_text(tmp_path, make_report(final_markdown=body))

        assert text.count("## 引用核验") == 1
        assert text.count("## 参考文献") == 1

    def test_appends_section_when_all_entries_were_intercepted(self, tmp_path: Path) -> None:
        """拦截到参考文献列表为空时，统计章节仍要出现（否则产物无法审计）。"""
        text = exported_text(tmp_path, make_report(bibliography=[]))

        assert EXPECTED_SECTION in text
        assert "## 参考文献" not in text

    def test_legacy_report_has_no_section_and_no_marker(self, tmp_path: Path) -> None:
        text = exported_text(tmp_path, legacy_report())

        assert "## 引用核验" not in text
        assert "（核验：" not in text
        # 无核验信息的老数据渲染保持原样（逐行拼接，含既有 source_id 括号与链接后缀）
        assert LEGACY_LINES in text

    def test_unsafe_links_still_rejected(self, tmp_path: Path) -> None:
        exporter = LiteratureExporter(tmp_path / "artifacts")
        report = make_report(final_markdown=f"{BODY}\n[x](javascript:alert(1))\n")

        with pytest.raises(ValidationException):
            exporter.export_markdown(USER_A, TASK_ID, report)

    def test_reference_line_marks_retraction_independently(self, tmp_path: Path) -> None:
        """撤稿状态与四态核验独立展示：已撤稿条目必须同时保留核验结论与撤稿标记。"""
        report = make_report()
        report.bibliography[0]["is_retracted"] = True

        text = exported_text(tmp_path, report)

        assert "（核验：已验证；已撤稿）" in text
        # 其他条目没有撤稿信息 → 未知，而不是「未标记撤稿」
        assert "（核验：未找到；撤稿状态未知）" in text

    def test_reference_line_marks_false_as_not_marked_not_safe(self, tmp_path: Path) -> None:
        """`False` 只代表来源未标记撤稿；标记里不得出现「安全」这类结论。"""
        report = make_report()
        report.bibliography[0]["is_retracted"] = False

        text = exported_text(tmp_path, report)

        assert "（核验：已验证；未标记撤稿）" in text
        assert "安全" not in text


class TestDocxVerificationSection:
    def test_docx_contains_verification_section(self, tmp_path: Path) -> None:
        exporter = LiteratureExporter(tmp_path / "artifacts")
        artifact = exporter.export_docx(USER_A, TASK_ID, make_report())
        assert artifact.kind == "docx"
        target = exporter.resolve_for_user(USER_A, TASK_ID, artifact.artifact_id)

        document = Document(str(target))
        headings = [
            paragraph.text
            for paragraph in document.paragraphs
            if paragraph.style.name.startswith("Heading")
        ]
        joined = "\n".join(paragraph.text for paragraph in document.paragraphs)

        assert "引用核验" in headings
        assert "已验证 1 条" in joined
        assert "元数据不符 1 条" in joined
        assert "未核验 0 条" in joined
        assert "已拦截 1 条" in joined
        assert "拦截开关：开启" in joined
        # 参考文献行的核验标记同样进入 DOCX
        assert "RAG Evaluation Survey" in joined
        assert "（核验：已验证；撤稿状态未知）" in joined

    def test_docx_legacy_report_unchanged(self, tmp_path: Path) -> None:
        exporter = LiteratureExporter(tmp_path / "artifacts")
        artifact = exporter.export_docx(USER_A, TASK_ID, legacy_report())
        target = exporter.resolve_for_user(USER_A, TASK_ID, artifact.artifact_id)

        document = Document(str(target))
        joined = "\n".join(paragraph.text for paragraph in document.paragraphs)
        assert "引用核验" not in joined
        assert "（核验：" not in joined
