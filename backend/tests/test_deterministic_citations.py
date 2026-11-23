"""确定性适配器的引用口径（Compose 冒烟/离线演练的引用一致性）。"""

from __future__ import annotations

from app.llm.deterministic import executor_content


def _prompt(*evidence_lines: str, dependency: str = "") -> str:
    parts = ["## 证据", *evidence_lines]
    if dependency:
        parts.append(f"\n依赖节点输出：\n{dependency}\n")
    return "\n\n".join(parts)


class TestExecutorContentCitations:
    def test_cites_metadata_and_excerpt_evidence_together(self) -> None:
        """元数据行（[E1 metadata]）与分块行（[E1] doc=）都要被引用。

        2026-11-23 smoke 实测：只认 doc= 行时元数据编号漏引 → 参考文献出现孤儿条目
        → 引用一致性闸门判任务 failed。
        """
        prompt = _prompt(
            "[E1 metadata] AR-RAG: Autoregressive Retrieval Augmentation",
            "[E2 metadata] Ragas: Automated Evaluation of Retrieval Augmented Generation",
            "[E3] doc=11111111-1111-1111-1111-111111111111 chunk=2222-2222",
            "检索增强生成通过外部知识降低事实性错误。",
        )

        content = executor_content(prompt)

        assert "[E1]" in content and "[E2]" in content and "[E3]" in content

    def test_metadata_only_prompt_is_still_cited(self) -> None:
        prompt = _prompt("[E1 metadata] 文献甲", "[E2 metadata] 文献乙")

        content = executor_content(prompt)

        assert "[E1]" in content and "[E2]" in content

    def test_dependency_markers_do_not_create_out_of_range_citations(self) -> None:
        """依赖输出正文里的 [E9] 不是证据行，不能带进新正文（会被判越界）。"""
        prompt = _prompt(
            "[E1 metadata] 文献甲",
            dependency="[n2] 上一节点正文引用了 [E9]，但本节点只有 1 条证据。",
        )

        content = executor_content(prompt)

        assert "[E9]" not in content
        assert "[E1]" in content

    def test_without_any_evidence_marks_inference(self) -> None:
        content = executor_content(_prompt("（无检索证据）"))

        assert "模型推断" in content
        assert "[E" not in content
