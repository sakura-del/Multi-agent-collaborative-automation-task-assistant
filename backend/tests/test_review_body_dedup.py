"""正文冗余治理（Issue #7）：复盘正文不得机械拼接全部节点输出。

生产取证（任务 `45539af8` / `80b09d27`）：正文 57 段里 26 段完全重复（45.6%），
`## 参考文献` 出现 3 次，`### 1. Ragas…` 这类条目标题出现 3 次。
根因：`ReviewerAgent._build_literature_review()` 把**每个**节点的 `content` 直接
`"\n\n".join(body_parts)`；而 extract 节点的逐条抽取、write 节点的综述正文、
validate/review 节点的依赖汇总（本身又是各节点拼接）互相包含。

本文件锁定四条性质：
1. **只保留作者节点**（extract/write）的正文，机械汇总节点不再进正文；
2. 多作者节点之间仍可能重复：同一段内容**只出现一次**（去重，保留首次出现顺序）；
3. 摘要必须取自作者正文，而不是汇总节点的拼接文本；
4. **没有类型信息的历史负载**（旧持久化数据）行为不变——不能凭空猜测后丢内容。
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from app.orchestration.agents.reviewer import ReviewerAgent

TOPIC = "检索增强生成的评估方法"

EXTRACT = """## 五篇文献证据提取

### 1. Ragas: Automated Evaluation of Retrieval Augmented Generation

- **研究内容：**提出 Ragas，用于 RAG 流程的无需参考答案评估 [E1]。

### 2. EVOR: Evolving Retrieval for Code Generation

- **证据提取结果：**现有证据未提供该文方法细节 [E2]。"""

WRITE = """## 中文 RAG 评估方法综述

RAG 评估不仅要考察最终答案，也要衡量检索内容与生成结果之间的关系 [E1]。

在图像生成领域，现有材料提供的是检索增强图像生成的背景 [E2]。"""

#: 生产形态：validate 节点内容是各依赖节点输出的机械拼接
VALIDATE = f"[n3] {EXTRACT}\n\n[n4] {WRITE}"

SHARED_PARAGRAPH = "RAG 评估不仅要考察最终答案，也要衡量检索内容与生成结果之间的关系 [E1]。"


def _result(
    node_id: str,
    content: str,
    *,
    node_type: str | None = None,
    artifacts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "node_id": node_id,
        "content": content,
        "evidence": [],
        "artifacts": artifacts or [],
        "duration_ms": 1,
    }
    if node_type is not None:
        payload["node_type"] = node_type
    return payload


def _input(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "trace_id": "trace-issue-7",
        "topic": TOPIC,
        "results": results,
        "validations": [],
        "event_stats": {"event_count": 3},
    }


def _body_of(markdown: str) -> str:
    """取出正文区（摘要段落之后、局限性之前）。"""
    rest = markdown.split("## 摘要", 1)[-1]
    rest = rest.split("## 局限性", 1)[0]
    blocks = rest.split("\n\n")
    # 第一块是摘要段落本身，正文从其后开始
    if blocks and blocks[0].strip():
        blocks = blocks[1:]
    return "\n\n".join(blocks)


def _paragraphs(text: str) -> list[str]:
    import re

    return [re.sub(r"\s+", " ", item).strip() for item in text.split("\n\n") if item.strip()]


class TestAuthorNodesOnly:
    async def test_aggregated_node_content_is_not_appended(self) -> None:
        """validate 节点的机械汇总不得进入正文（它是其他节点的重复）。"""
        report = await ReviewerAgent().execute(
            _input(
                [
                    _result("n3", EXTRACT, node_type="extract"),
                    _result("n4", WRITE, node_type="write"),
                    _result("n5", VALIDATE, node_type="validate"),
                ]
            )
        )

        body = _body_of(report["final_markdown"])

        assert EXTRACT.strip() in body or "五篇文献证据提取" in body
        assert "中文 RAG 评估方法综述" in body
        # 汇总节点带 `[nX]` 前缀的拼接痕迹不得出现在正文
        assert "[n3]" not in body
        assert "[n4]" not in body

    async def test_reference_section_appears_once(self) -> None:
        """生产产物里 `## 参考文献` 出现 3 次——正文不得再引入参考文献章节。"""
        report = await ReviewerAgent().execute(
            _input(
                [
                    _result("n3", EXTRACT, node_type="extract"),
                    _result("n4", WRITE, node_type="write"),
                    _result("n5", VALIDATE, node_type="validate"),
                ]
            )
        )

        assert report["final_markdown"].count("## 参考文献") <= 1

    async def test_author_node_own_bibliography_is_superseded(self) -> None:
        """作者节点自带「参考文献」章节时，以复盘统一生成的为准（不得出现两份）。"""
        write_with_bib = (
            f"{WRITE}\n\n## 参考文献\n\n- [1] Ragas — https://arxiv.org/abs/2309.15217v2\n"
            "- [2] EVOR — https://arxiv.org/abs/2406.00001v1"
        )
        report = await ReviewerAgent().execute(
            _input(
                [
                    _result(
                        "n3",
                        EXTRACT,
                        node_type="extract",
                        artifacts=[
                            {"title": "Ragas", "source_url": "https://arxiv.org/abs/2309.15217v2"}
                        ],
                    ),
                    _result("n4", write_with_bib, node_type="write"),
                ]
            )
        )

        markdown = report["final_markdown"]
        assert markdown.count("## 参考文献") == 1
        # 复盘生成的参考文献必须保留
        assert "https://arxiv.org/abs/2309.15217v2" in markdown
        # 正文其他内容不得被连带删除
        assert "中文 RAG 评估方法综述" in markdown
        assert "在图像生成领域" in markdown

    async def test_duplicate_paragraphs_are_collapsed(self) -> None:
        """两个作者节点含同一段落时，正文里只保留一次。"""
        duplicated_extract = f"{EXTRACT}\n\n{SHARED_PARAGRAPH}"
        report = await ReviewerAgent().execute(
            _input(
                [
                    _result("n3", duplicated_extract, node_type="extract"),
                    _result("n4", WRITE, node_type="write"),
                ]
            )
        )

        body = _body_of(report["final_markdown"])

        assert body.count(SHARED_PARAGRAPH) == 1
        paragraphs = _paragraphs(body)
        assert len(paragraphs) - len(set(paragraphs)) == 0

    async def test_abstract_comes_from_author_body(self) -> None:
        """摘要不得取自汇总节点的拼接文本。"""
        prose = "现有证据显示 RAG 评估可分为检索质量与生成质量两个维度 [E1]。"
        report = await ReviewerAgent().execute(
            _input(
                [
                    _result("n3", f"{prose}\n\n{EXTRACT}", node_type="extract"),
                    _result("n5", VALIDATE, node_type="validate"),
                ]
            )
        )

        abstract = report["stats"]["abstract"]
        assert abstract.startswith("现有证据显示")
        assert not abstract.startswith("[n")


class TestBackwardCompatibility:
    async def test_results_without_node_type_keep_all_content(self) -> None:
        """历史负载没有 `node_type`：无法判定角色，保持原行为（不丢内容）。"""
        report = await ReviewerAgent().execute(
            _input(
                [
                    _result("n3", EXTRACT),
                    _result("n4", WRITE),
                ]
            )
        )

        body = _body_of(report["final_markdown"])

        assert "五篇文献证据提取" in body
        assert "中文 RAG 评估方法综述" in body

    async def test_single_author_node_output_is_unchanged(self) -> None:
        report = await ReviewerAgent().execute(_input([_result("n4", WRITE, node_type="write")]))

        body = _body_of(report["final_markdown"])

        assert body.strip() == WRITE.strip()
        assert report["final_markdown"].count(WRITE.strip()) == 1


class TestExplicitContentRole:
    async def test_explicit_aggregated_role_excludes_content(self) -> None:
        """`content_role` 显式标注同样生效（供未带 node_type 的调用方使用）。"""
        result = _result("n5", VALIDATE)
        result["content_role"] = "aggregated"
        report = await ReviewerAgent().execute(
            _input([_result("n4", WRITE, node_type="write"), result])
        )

        body = _body_of(report["final_markdown"])

        assert "中文 RAG 评估方法综述" in body
        assert "[n3]" not in body


@pytest.mark.asyncio
class TestWorkflowForwardsNodeType:
    """工作流必须把节点类型交给复盘，否则复盘无法区分作者正文与机械汇总。"""

    async def test_review_payload_carries_node_type(self) -> None:
        from uuid import uuid4

        from app.orchestration.contracts import ExecutionResult
        from app.orchestration.workflows.literature_review import LiteratureReviewWorkflow

        calls: list[dict[str, Any]] = []

        class _FakeReviewer:
            async def execute(self, input_data: dict[str, Any]) -> dict[str, Any]:
                calls.append(input_data)
                return {
                    "final_markdown": "# 综述",
                    "bibliography": [],
                    "scores": {},
                    "issues": [],
                    "stats": {},
                }

        class _Task:
            id = uuid4()
            title = TOPIC
            description = TOPIC
            config: ClassVar[dict[str, Any]] = {}

        workflow = LiteratureReviewWorkflow(reviewer=_FakeReviewer())
        await workflow.review(
            _Task(),
            [ExecutionResult(node_id="n4", content="正文 [E1]", duration_ms=1, node_type="write")],
        )

        assert calls[0]["results"][0]["node_id"] == "n4"
        assert calls[0]["results"][0]["node_type"] == "write"

    async def test_execute_node_sets_node_type_for_author_and_aggregate(self) -> None:
        """execute_node 必须为两类节点都标注类型（复盘据此筛选正文）。"""
        from uuid import uuid4

        from app.orchestration.contracts import PlanNode, TaskPlan
        from app.orchestration.workflows.literature_review import LiteratureReviewWorkflow

        class _Task:
            id = uuid4()
            user_id = uuid4()
            title = TOPIC
            description = TOPIC
            config: ClassVar[dict[str, Any]] = {}

        plan = TaskPlan(
            version=1,
            topic=TOPIC,
            research_questions=["RAG 如何评估"],
            search_queries=["RAG evaluation"],
            acceptance_criteria=["引用可追溯"],
            nodes=[
                PlanNode(id="n3", type="extract", title="抽取"),
                PlanNode(id="n5", type="validate", title="校验", dependencies=["n3"]),
            ],
        )
        workflow = LiteratureReviewWorkflow().use_plan(plan)

        aggregate = await workflow.execute_node(
            _Task(),
            plan.nodes[1],
            dependency_outputs={"n3": {"result": "抽取内容"}},
        )

        assert aggregate.node_type == "validate"
        assert aggregate.content
