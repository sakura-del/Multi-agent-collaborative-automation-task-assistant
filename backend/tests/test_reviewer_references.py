"""T5 复盘引用核验统计与拦截开关测试（计划 §2.6 后半段）。

铁律（本文件必须遵守）：
- **不产生任何真实网络请求**：核验器要么是注入的 fake（固定四态结论），
  要么是**真实 `ReferenceVerifier` + fake `MetadataSource`**；
  本文件不 import ``crossref.py`` / ``openalex.py``，不发起任何 HTTP 调用；
- 语义以计划 §2.3（判定四态）与 §2.6（``stats["reference_verification"]`` + 拦截开关）为准：
  候选来自**最终 bibliography**；拦截只剔除 ``blocked_keys``（元数据不符 / 未找到），
  ``unverified`` 必须保留；核验失败（异常）绝不能让复盘整体失败。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from uuid import UUID

from app.core.exceptions import UpstreamTemporaryError
from app.knowledge.services.reference_verifier import ReferenceVerifier
from app.orchestration.agents.reviewer import ReviewerAgent
from app.tools.literature.models import (
    MetadataRecord,
    ReferenceCandidate,
    ReferenceVerdict,
    ReferenceVerification,
)

TOPIC = "检索增强生成的评估方法"

VERIFIED_TITLE = "RAG Evaluation Survey"
MISMATCH_TITLE = "Evidence Tracing for Retrieval"
NOT_FOUND_TITLE = "Fabricated Reference On Graph Neural Nets"
UNVERIFIED_TITLE = "Unstable Upstream Metadata Study"

#: 检索节点 artifacts → 最终 bibliography（第四条故意不带 DOI，走标题键）
ARTIFACTS: list[dict[str, Any]] = [
    {
        "title": VERIFIED_TITLE,
        "source_url": "https://doi.org/10.1234/verified",
        "source_id": "10.1234/verified",
    },
    {
        "title": MISMATCH_TITLE,
        "source_url": "https://arxiv.org/abs/2401.12342v1",
        "source_id": "2401.12342v1",
    },
    {
        "title": NOT_FOUND_TITLE,
        "source_url": "https://arxiv.org/abs/2401.12343v1",
        "source_id": "2401.12343v1",
    },
    {
        "title": UNVERIFIED_TITLE,
        "source_url": "https://arxiv.org/abs/2401.12344v1",
        "source_id": "2401.12344v1",
    },
]

VERDICTS: dict[str, ReferenceVerdict] = {
    VERIFIED_TITLE: ReferenceVerdict.VERIFIED,
    MISMATCH_TITLE: ReferenceVerdict.METADATA_MISMATCH,
    NOT_FOUND_TITLE: ReferenceVerdict.NOT_FOUND,
    UNVERIFIED_TITLE: ReferenceVerdict.UNVERIFIED,
}

BODY = "## 方法\n\n本节比较检索质量与证据可追溯性指标 [E1]。"


# ===== 测试替身 =====


class FakeVerifier:
    """注入固定四态结论的 fake 核验器：不联网、不做真实判定。"""

    def __init__(
        self,
        verdicts: Mapping[str, ReferenceVerdict] | None = None,
        *,
        intercept: bool = False,
        error: Exception | None = None,
        cached_first: bool = False,
    ) -> None:
        self._verdicts = dict(verdicts or {})
        self._intercept = intercept
        self._error = error
        self._cached_first = cached_first
        self.calls = 0
        self.candidates: list[ReferenceCandidate] = []

    @property
    def intercept_unverified(self) -> bool:
        return self._intercept

    async def verify_many(
        self, candidates: Sequence[ReferenceCandidate]
    ) -> list[ReferenceVerification]:
        self.calls += 1
        self.candidates = list(candidates)
        if self._error is not None:
            raise self._error
        return [
            ReferenceVerification(
                reference_key=candidate.key,
                title=candidate.title,
                verdict=self._verdicts.get(candidate.title, ReferenceVerdict.VERIFIED),
                reason=f"核验理由：{candidate.title}",
                cached=self._cached_first and index == 0,
            )
            for index, candidate in enumerate(candidates)
        ]


class MinimalVerifier:
    """只实现 ``verify_many`` 的最小替身：缺 ``intercept_unverified`` 时按「开关关闭」处理。"""

    async def verify_many(
        self, candidates: Sequence[ReferenceCandidate]
    ) -> list[ReferenceVerification]:
        return [
            ReferenceVerification(
                reference_key=candidate.key,
                title=candidate.title,
                verdict=ReferenceVerdict.VERIFIED,
                reason="最小替身一律判已验证",
            )
            for candidate in candidates
        ]


class FakeMetadataSource:
    """实现 ``MetadataSource`` 协议的最小 fake（无网络）：只认一个固定 DOI。"""

    def __init__(
        self,
        *,
        record: MetadataRecord | None = None,
        error: Exception | None = None,
    ) -> None:
        self.record = record
        self.error = error
        self.doi_queries: list[str] = []
        self.title_queries: list[str] = []

    async def lookup_doi(self, doi: str) -> MetadataRecord | None:
        self.doi_queries.append(doi)
        if self.error is not None:
            raise self.error
        if self.record is not None and doi == self.record.doi:
            return self.record
        return None

    async def search_by_title(self, title: str) -> list[MetadataRecord]:
        self.title_queries.append(title)
        if self.error is not None:
            raise self.error
        return []


def make_input(
    artifacts: Sequence[Mapping[str, Any]] = tuple(ARTIFACTS),
    *,
    content: str = BODY,
) -> dict[str, Any]:
    """构造文献场景复盘输入（只含已持久化结果，不触发任何外部工具）。"""
    return {
        "trace_id": "trace-t5",
        "topic": TOPIC,
        "results": [
            {
                "node_id": "n3",
                "content": content,
                "evidence": [],
                "artifacts": [dict(item) for item in artifacts],
                "duration_ms": 5,
                "prompt_tokens": 10,
                "completion_tokens": 5,
            }
        ],
        "validations": [],
        "event_stats": {"event_count": 7},
    }


def bibliography_indexes(markdown: str) -> list[int]:
    """提取「参考文献」章节里的编号序列（用于断言拦截后重新连续编号）。"""
    section = markdown.split("## 参考文献", 1)[1].split("\n## ", 1)[0]
    return [int(match) for match in re.findall(r"^- \[(\d+)\]", section, re.M)]


def expected_bibliography() -> list[dict[str, Any]]:
    """未注入核验器时 bibliography 的历史形状（不得新增任何键）。"""
    return [
        {
            "index": index,
            "title": artifact["title"],
            "source_url": artifact["source_url"],
            "source_id": artifact["source_id"],
        }
        for index, artifact in enumerate(ARTIFACTS, start=1)
    ]


# ===== 未注入核验器：旧行为必须完全一致 =====


class TestReviewerWithoutVerifier:
    """``reference_verifier=None``（默认）时不得产生新 stats 键与新标记。"""

    async def test_stats_and_bibliography_unchanged(self) -> None:
        report = await ReviewerAgent().execute(make_input())

        assert "reference_verification" not in report["stats"]
        assert report["bibliography"] == expected_bibliography()
        assert "## 引用核验" not in report["final_markdown"]
        assert "（核验：" not in report["final_markdown"]

    async def test_verifier_is_optional_keyword_argument(self) -> None:
        """构造签名向后兼容：既有的 ``ReviewerAgent()`` / ``ReviewerAgent(llm=...)`` 不变。"""
        agent = ReviewerAgent()
        assert agent.reference_verifier is None
        assert ReviewerAgent(reference_verifier=None).reference_verifier is None


# ===== 注入核验器：统计、标记、拦截 =====


class TestReviewerVerificationStats:
    """核验统计写入 stats，bibliography 条目带核验标记。"""

    async def test_summary_structure_and_entry_markers(self) -> None:
        fake = FakeVerifier(VERDICTS, cached_first=True)
        report = await ReviewerAgent(reference_verifier=fake).execute(make_input())

        assert report["stats"]["reference_verification"] == {
            "total": 4,
            "verified": 1,
            "metadata_mismatch": 1,
            "not_found": 1,
            "unverified": 1,
            "cached_hits": 1,
            "dropped": 0,
            "intercepted": False,
        }

        bibliography = report["bibliography"]
        assert [entry["index"] for entry in bibliography] == [1, 2, 3, 4]
        assert [entry["verification_status"] for entry in bibliography] == [
            "verified",
            "metadata_mismatch",
            "not_found",
            "unverified",
        ]
        assert all(entry["verification_reason"] for entry in bibliography)

        # 候选来自最终 bibliography；source_url 是 doi.org 链接时 key/doi 用归一化 DOI
        assert [candidate.title for candidate in fake.candidates] == [
            VERIFIED_TITLE,
            MISMATCH_TITLE,
            NOT_FOUND_TITLE,
            UNVERIFIED_TITLE,
        ]
        assert fake.candidates[0].key == "10.1234/verified"
        assert fake.candidates[0].doi == "10.1234/verified"
        assert fake.candidates[3].key == "unstable upstream metadata study"

        markdown = report["final_markdown"]
        assert "## 引用核验" in markdown
        for label in ("已验证", "元数据不符", "未找到", "未核验"):
            assert f"（核验：{label}）" in markdown
        assert "已验证 1 条" in markdown
        assert "已拦截 0 条" in markdown
        assert "拦截开关：关闭" in markdown
        assert bibliography_indexes(markdown) == [1, 2, 3, 4]

    async def test_intercept_drops_blocked_and_renumbers(self) -> None:
        fake = FakeVerifier(VERDICTS, intercept=True)
        report = await ReviewerAgent(reference_verifier=fake).execute(make_input())

        bibliography = report["bibliography"]
        assert [entry["title"] for entry in bibliography] == [VERIFIED_TITLE, UNVERIFIED_TITLE]
        assert [entry["index"] for entry in bibliography] == [1, 2]

        summary = report["stats"]["reference_verification"]
        assert summary["dropped"] == 2
        assert summary["intercepted"] is True
        # 统计仍覆盖核验过的全部候选（审计口径），只有展示列表被裁剪
        assert summary["total"] == 4
        assert summary["verified"] == 1
        assert summary["unverified"] == 1

        markdown = report["final_markdown"]
        assert bibliography_indexes(markdown) == [1, 2]
        assert MISMATCH_TITLE not in markdown
        assert NOT_FOUND_TITLE not in markdown
        assert "已拦截 2 条" in markdown
        assert "拦截开关：开启" in markdown

    async def test_unverified_entries_are_kept_when_intercepting(self) -> None:
        """外部源全部故障（UNVERIFIED）时一条都不能被剔除：降级态不得惩罚/误伤。"""
        fake = FakeVerifier(
            {title: ReferenceVerdict.UNVERIFIED for title in VERDICTS},
            intercept=True,
        )
        report = await ReviewerAgent(reference_verifier=fake).execute(make_input())

        assert [entry["index"] for entry in report["bibliography"]] == [1, 2, 3, 4]
        assert all(entry["verification_status"] == "unverified" for entry in report["bibliography"])

        summary = report["stats"]["reference_verification"]
        assert summary["total"] == 4
        assert summary["unverified"] == 4
        assert summary["dropped"] == 0

        markdown = report["final_markdown"]
        assert "未核验 4 条" in markdown
        assert "已拦截 0 条" in markdown
        assert bibliography_indexes(markdown) == [1, 2, 3, 4]

    async def test_missing_intercept_attribute_treated_as_off(self) -> None:
        """注入对象没有 ``intercept_unverified`` 时按关闭处理，不得影响复盘。"""
        report = await ReviewerAgent(reference_verifier=MinimalVerifier()).execute(make_input())

        assert [entry["index"] for entry in report["bibliography"]] == [1, 2, 3, 4]
        assert all(entry["verification_status"] == "verified" for entry in report["bibliography"])
        summary = report["stats"]["reference_verification"]
        assert summary["verified"] == 4
        assert summary["intercepted"] is False
        assert summary["dropped"] == 0

    async def test_entry_without_title_is_kept_without_marker(self) -> None:
        """无法构造候选的条目（无标题）保留且不加标记，统计口径保持一致。"""
        artifacts = [
            dict(ARTIFACTS[0]),
            {"source_url": "https://arxiv.org/abs/2401.12345v1"},
        ]
        fake = FakeVerifier(VERDICTS, intercept=True)
        report = await ReviewerAgent(reference_verifier=fake).execute(
            make_input(artifacts=artifacts)
        )

        bibliography = report["bibliography"]
        assert len(bibliography) == 2
        assert bibliography[0]["verification_status"] == "verified"
        assert "verification_status" not in bibliography[1]
        assert report["stats"]["reference_verification"]["total"] == 1
        assert report["stats"]["reference_verification"]["dropped"] == 0


# ===== 核验失败：绝不拖垮复盘 =====


class TestReviewerVerificationFailure:
    """核验器抛异常时降级为「全部未核验」，复盘必须照常产出。"""

    async def test_verifier_exception_degrades_to_unverified(self) -> None:
        fake = FakeVerifier(
            VERDICTS,
            intercept=True,
            error=RuntimeError("crossref 连接被重置"),
        )
        report = await ReviewerAgent(reference_verifier=fake).execute(make_input())

        assert report["final_markdown"].startswith(f"# {TOPIC}")
        assert "## 参考文献" in report["final_markdown"]

        # 异常路径一条都不能删（拦截开关也不得据此剔除）
        assert [entry["index"] for entry in report["bibliography"]] == [1, 2, 3, 4]
        assert all(entry["verification_status"] == "unverified" for entry in report["bibliography"])
        assert "异常" in report["bibliography"][0]["verification_reason"]

        summary = report["stats"]["reference_verification"]
        assert summary["total"] == 4
        assert summary["unverified"] == 4
        assert summary["dropped"] == 0
        assert summary["intercepted"] is True

        markdown = report["final_markdown"]
        assert "未核验 4 条" in markdown
        assert "已拦截 0 条" in markdown
        assert bibliography_indexes(markdown) == [1, 2, 3, 4]

    async def test_verification_failure_keeps_other_stats(self) -> None:
        """核验失败不得影响既有 stats 字段。"""
        fake = FakeVerifier(error=ValueError("核验器内部错误"))
        report = await ReviewerAgent(reference_verifier=fake).execute(make_input())

        assert report["stats"]["node_count"] == 1
        assert report["stats"]["event_count"] == 7
        assert report["stats"]["documents"] == 4
        assert report["stats"]["title"] == TOPIC


# ===== 与真实核验器的接线（fake MetadataSource，无网络） =====


class TestRealVerifierIntegration:
    """真实 `ReferenceVerifier` + fake `MetadataSource`：证明接线与冻结口径一致。"""

    async def test_real_verifier_drives_interception_and_renumbering(self) -> None:
        source = FakeMetadataSource(
            record=MetadataRecord(
                source="crossref",
                doi="10.1234/verified",
                title=VERIFIED_TITLE,
                authors=["Wei Zhang"],
                year=2025,
            )
        )
        verifier = ReferenceVerifier(crossref=source, intercept_unverified=True)
        report = await ReviewerAgent(reference_verifier=verifier).execute(make_input())

        # DOI 命中的第一条判「已验证」，其余无 DOI 且标题检索无命中 → 「未找到」被拦截
        assert [entry["title"] for entry in report["bibliography"]] == [VERIFIED_TITLE]
        assert [entry["index"] for entry in report["bibliography"]] == [1]

        summary = report["stats"]["reference_verification"]
        assert summary["total"] == 4
        assert summary["verified"] == 1
        assert summary["not_found"] == 3
        assert summary["dropped"] == 3
        assert summary["intercepted"] is True
        assert source.doi_queries == ["10.1234/verified"]

        markdown = report["final_markdown"]
        assert bibliography_indexes(markdown) == [1]
        assert "已拦截 3 条" in markdown

    async def test_real_verifier_downgrade_is_never_intercepted(self) -> None:
        """真实核验器在源故障时降级为 UNVERIFIED：拦截开关不得据此剔条目。"""
        verifier = ReferenceVerifier(
            crossref=FakeMetadataSource(error=UpstreamTemporaryError("crossref 503")),
            intercept_unverified=True,
        )
        report = await ReviewerAgent(reference_verifier=verifier).execute(make_input())

        assert [entry["index"] for entry in report["bibliography"]] == [1, 2, 3, 4]
        assert all(entry["verification_status"] == "unverified" for entry in report["bibliography"])
        summary = report["stats"]["reference_verification"]
        assert summary["unverified"] == 4
        assert summary["dropped"] == 0
        assert "未核验 4 条" in report["final_markdown"]


class TestReviewerToExporterFlow:
    """复盘产物 → 导出：核验章节只出现一次（导出侧不得重复追加）。"""

    async def test_exported_markdown_has_single_verification_section(self, tmp_path: Path) -> None:
        from app.orchestration.contracts import ReviewReport
        from app.tools.literature.exporter import LiteratureExporter

        fake = FakeVerifier(VERDICTS, intercept=True)
        result = await ReviewerAgent(reference_verifier=fake).execute(make_input())
        report = ReviewReport.model_validate(
            {
                "final_markdown": result["final_markdown"],
                "bibliography": result["bibliography"],
                "scores": result["scores"],
                "issues": result["issues"],
                "stats": result["stats"],
            }
        )

        user_id = UUID("99999999-9999-9999-9999-999999999999")
        task_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        exporter = LiteratureExporter(tmp_path / "artifacts")
        artifact = exporter.export_markdown(user_id, task_id, report)
        text = exporter.resolve_for_user(user_id, task_id, artifact.artifact_id).read_text(
            encoding="utf-8"
        )

        assert text.count("## 引用核验") == 1
        assert "已拦截 2 条" in text
        assert MISMATCH_TITLE not in text
        assert NOT_FOUND_TITLE not in text
        assert UNVERIFIED_TITLE in text
